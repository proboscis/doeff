//! DOEFF007: No Mutable Argument Mutations
//!
//! Functions should not mutate dict, list, or set arguments.

use crate::models::{RuleContext, Severity, Violation};
use crate::rules::base::LintRule;
use rustpython_ast::{Arguments, Expr, Stmt, StmtAsyncFunctionDef, StmtFunctionDef};
use std::collections::HashSet;

pub struct NoMutableArgumentMutationsRule;

impl NoMutableArgumentMutationsRule {
    pub fn new() -> Self {
        Self
    }

    fn get_parameter_names(args: &Arguments) -> HashSet<String> {
        let mut names = HashSet::new();

        for arg in &args.posonlyargs {
            names.insert(arg.def.arg.to_string());
        }
        for arg in &args.args {
            names.insert(arg.def.arg.to_string());
        }
        for arg in &args.kwonlyargs {
            names.insert(arg.def.arg.to_string());
        }
        if let Some(arg) = &args.vararg {
            names.insert(arg.arg.to_string());
        }
        if let Some(arg) = &args.kwarg {
            names.insert(arg.arg.to_string());
        }

        names
    }

    fn is_mutation_method(method_name: &str) -> bool {
        matches!(
            method_name,
            "append"
                | "extend"
                | "insert"
                | "remove"
                | "pop"
                | "clear"
                | "sort"
                | "reverse"
                | "update"
                | "popitem"
                | "setdefault"
                | "add"
                | "discard"
                | "intersection_update"
                | "difference_update"
                | "symmetric_difference_update"
        )
    }

    fn references_parameter(expr: &Expr, param_names: &HashSet<String>) -> Option<String> {
        match expr {
            Expr::Name(name) => {
                if param_names.contains(name.id.as_str()) {
                    Some(name.id.to_string())
                } else {
                    None
                }
            }
            Expr::Subscript(subscript) => Self::references_parameter(&subscript.value, param_names),
            _ => None,
        }
    }

    fn check_stmt(
        stmt: &Stmt,
        param_names: &HashSet<String>,
        func_name: &str,
        file_path: &str,
    ) -> Vec<Violation> {
        let mut violations = Vec::new();

        match stmt {
            Stmt::Expr(expr_stmt) => {
                if let Expr::Call(call) = &*expr_stmt.value {
                    if let Expr::Attribute(attr) = &*call.func {
                        if let Expr::Name(name) = &*attr.value {
                            if param_names.contains(name.id.as_str())
                                && Self::is_mutation_method(attr.attr.as_str())
                            {
                                violations.push(Violation::new(
                                    "DOEFF007".to_string(),
                                    format!(
                                        "Function '{}' mutates argument '{}' by calling '{}()'. \
                                         Return a new collection instead.",
                                        func_name, name.id, attr.attr
                                    ),
                                    expr_stmt.range.start().to_usize(),
                                    file_path.to_string(),
                                    Severity::Error,
                                ));
                            }
                        }
                    }
                }
            }
            Stmt::Assign(assign) => {
                for target in &assign.targets {
                    if let Expr::Subscript(subscript) = target {
                        if let Some(param_name) =
                            Self::references_parameter(&subscript.value, param_names)
                        {
                            violations.push(Violation::new(
                                "DOEFF007".to_string(),
                                format!(
                                    "Function '{}' mutates argument '{}' by assigning to index/key. \
                                     Return a new collection instead.",
                                    func_name, param_name
                                ),
                                assign.range.start().to_usize(),
                                file_path.to_string(),
                                Severity::Error,
                            ));
                        }
                    }
                }
            }
            Stmt::AugAssign(aug_assign) => {
                if let Expr::Subscript(subscript) = &*aug_assign.target {
                    if let Some(param_name) =
                        Self::references_parameter(&subscript.value, param_names)
                    {
                        violations.push(Violation::new(
                            "DOEFF007".to_string(),
                            format!(
                                "Function '{}' mutates argument '{}' through augmented assignment.",
                                func_name, param_name
                            ),
                            aug_assign.range.start().to_usize(),
                            file_path.to_string(),
                            Severity::Error,
                        ));
                    }
                }
            }
            Stmt::Delete(delete) => {
                for target in &delete.targets {
                    if let Expr::Subscript(subscript) = target {
                        if let Some(param_name) =
                            Self::references_parameter(&subscript.value, param_names)
                        {
                            violations.push(Violation::new(
                                "DOEFF007".to_string(),
                                format!(
                                    "Function '{}' mutates argument '{}' by deleting element.",
                                    func_name, param_name
                                ),
                                delete.range.start().to_usize(),
                                file_path.to_string(),
                                Severity::Error,
                            ));
                        }
                    }
                }
            }
            Stmt::If(if_stmt) => {
                for s in &if_stmt.body {
                    violations.extend(Self::check_stmt(s, param_names, func_name, file_path));
                }
                for s in &if_stmt.orelse {
                    violations.extend(Self::check_stmt(s, param_names, func_name, file_path));
                }
            }
            Stmt::While(while_stmt) => {
                for s in &while_stmt.body {
                    violations.extend(Self::check_stmt(s, param_names, func_name, file_path));
                }
            }
            Stmt::For(for_stmt) => {
                for s in &for_stmt.body {
                    violations.extend(Self::check_stmt(s, param_names, func_name, file_path));
                }
            }
            Stmt::With(with_stmt) => {
                for s in &with_stmt.body {
                    violations.extend(Self::check_stmt(s, param_names, func_name, file_path));
                }
            }
            Stmt::Try(try_stmt) => {
                for s in &try_stmt.body {
                    violations.extend(Self::check_stmt(s, param_names, func_name, file_path));
                }
                for handler in &try_stmt.handlers {
                    if let rustpython_ast::ExceptHandler::ExceptHandler(h) = handler {
                        for s in &h.body {
                            violations.extend(Self::check_stmt(s, param_names, func_name, file_path));
                        }
                    }
                }
            }
            _ => {}
        }

        violations
    }
}

impl LintRule for NoMutableArgumentMutationsRule {
    fn rule_id(&self) -> &str {
        "DOEFF007"
    }

    fn description(&self) -> &str {
        "Functions should not mutate dict/list/set arguments"
    }

    fn check(&self, context: &RuleContext) -> Vec<Violation> {
        match context.stmt {
            Stmt::FunctionDef(func) => {
                let param_names = Self::get_parameter_names(&func.args);
                let mut violations = Vec::new();
                for stmt in &func.body {
                    violations.extend(Self::check_stmt(
                        stmt,
                        &param_names,
                        func.name.as_str(),
                        context.file_path,
                    ));
                }
                violations
            }
            Stmt::AsyncFunctionDef(func) => {
                let param_names = Self::get_parameter_names(&func.args);
                let mut violations = Vec::new();
                for stmt in &func.body {
                    violations.extend(Self::check_stmt(
                        stmt,
                        &param_names,
                        func.name.as_str(),
                        context.file_path,
                    ));
                }
                violations
            }
            _ => vec![],
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rustpython_ast::Mod;
    use rustpython_parser::{parse, Mode};

    fn check_code(code: &str) -> Vec<Violation> {
        let ast = parse(code, Mode::Module, "test.py").unwrap();
        let rule = NoMutableArgumentMutationsRule::new();
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
    fn test_list_append() {
        let code = r#"
def process(items):
    items.append("new")
    return items
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("append"));
    }

    #[test]
    fn test_dict_assignment() {
        let code = r#"
def update(data):
    data["key"] = "value"
    return data
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_no_mutation() {
        let code = r#"
def process(items):
    new_items = items + ["new"]
    return new_items
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }
}




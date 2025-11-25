//! DOEFF004: No os.environ Access
//!
//! Forbid direct access to environment variables.

use crate::models::{RuleContext, Severity, Violation};
use crate::rules::base::LintRule;
use rustpython_ast::{Expr, Stmt};

pub struct NoOsEnvironRule;

impl NoOsEnvironRule {
    pub fn new() -> Self {
        Self
    }

    fn check_expr_for_environ(expr: &Expr, violations: &mut Vec<Violation>, file_path: &str) {
        match expr {
            // os.environ["KEY"] or os.environ.get("KEY")
            Expr::Subscript(subscript) => {
                if Self::is_os_environ(&subscript.value) {
                    violations.push(Violation::new(
                        "DOEFF004".to_string(),
                        "Direct access to os.environ is forbidden. \
                         Use dependency injection to receive configuration values."
                            .to_string(),
                        subscript.range.start().to_usize(),
                        file_path.to_string(),
                        Severity::Error,
                    ));
                }
                Self::check_expr_for_environ(&subscript.value, violations, file_path);
                Self::check_expr_for_environ(&subscript.slice, violations, file_path);
            }
            // os.environ.get() or os.getenv()
            Expr::Call(call) => {
                if let Expr::Attribute(attr) = &*call.func {
                    // os.environ.get()
                    if Self::is_os_environ(&attr.value) {
                        violations.push(Violation::new(
                            "DOEFF004".to_string(),
                            format!(
                                "Calling os.environ.{}() is forbidden. \
                                 Use dependency injection to receive configuration values.",
                                attr.attr
                            ),
                            call.range.start().to_usize(),
                            file_path.to_string(),
                            Severity::Error,
                        ));
                    }
                    // os.getenv()
                    if let Expr::Name(name) = &*attr.value {
                        if name.id.as_str() == "os" && attr.attr.as_str() == "getenv" {
                            violations.push(Violation::new(
                                "DOEFF004".to_string(),
                                "os.getenv() is forbidden. \
                                 Use dependency injection to receive configuration values."
                                    .to_string(),
                                call.range.start().to_usize(),
                                file_path.to_string(),
                                Severity::Error,
                            ));
                        }
                    }
                }
                Self::check_expr_for_environ(&call.func, violations, file_path);
                for arg in &call.args {
                    Self::check_expr_for_environ(arg, violations, file_path);
                }
            }
            Expr::Attribute(attr) => {
                Self::check_expr_for_environ(&attr.value, violations, file_path);
            }
            Expr::BinOp(binop) => {
                Self::check_expr_for_environ(&binop.left, violations, file_path);
                Self::check_expr_for_environ(&binop.right, violations, file_path);
            }
            Expr::Compare(compare) => {
                Self::check_expr_for_environ(&compare.left, violations, file_path);
                for comparator in &compare.comparators {
                    Self::check_expr_for_environ(comparator, violations, file_path);
                }
            }
            Expr::IfExp(ifexp) => {
                Self::check_expr_for_environ(&ifexp.test, violations, file_path);
                Self::check_expr_for_environ(&ifexp.body, violations, file_path);
                Self::check_expr_for_environ(&ifexp.orelse, violations, file_path);
            }
            _ => {}
        }
    }

    fn is_os_environ(expr: &Expr) -> bool {
        if let Expr::Attribute(attr) = expr {
            if attr.attr.as_str() == "environ" {
                if let Expr::Name(name) = &*attr.value {
                    return name.id.as_str() == "os";
                }
            }
        }
        false
    }

    fn check_stmt(stmt: &Stmt, violations: &mut Vec<Violation>, file_path: &str) {
        match stmt {
            Stmt::Expr(expr_stmt) => {
                Self::check_expr_for_environ(&expr_stmt.value, violations, file_path);
            }
            Stmt::Assign(assign) => {
                Self::check_expr_for_environ(&assign.value, violations, file_path);
            }
            Stmt::AnnAssign(ann_assign) => {
                if let Some(value) = &ann_assign.value {
                    Self::check_expr_for_environ(value, violations, file_path);
                }
            }
            Stmt::Return(ret) => {
                if let Some(value) = &ret.value {
                    Self::check_expr_for_environ(value, violations, file_path);
                }
            }
            Stmt::If(if_stmt) => {
                Self::check_expr_for_environ(&if_stmt.test, violations, file_path);
                for s in &if_stmt.body {
                    Self::check_stmt(s, violations, file_path);
                }
                for s in &if_stmt.orelse {
                    Self::check_stmt(s, violations, file_path);
                }
            }
            Stmt::While(while_stmt) => {
                Self::check_expr_for_environ(&while_stmt.test, violations, file_path);
                for s in &while_stmt.body {
                    Self::check_stmt(s, violations, file_path);
                }
            }
            Stmt::For(for_stmt) => {
                Self::check_expr_for_environ(&for_stmt.iter, violations, file_path);
                for s in &for_stmt.body {
                    Self::check_stmt(s, violations, file_path);
                }
            }
            Stmt::FunctionDef(func) => {
                for s in &func.body {
                    Self::check_stmt(s, violations, file_path);
                }
            }
            Stmt::AsyncFunctionDef(func) => {
                for s in &func.body {
                    Self::check_stmt(s, violations, file_path);
                }
            }
            Stmt::ClassDef(class_def) => {
                for s in &class_def.body {
                    Self::check_stmt(s, violations, file_path);
                }
            }
            Stmt::With(with_stmt) => {
                for s in &with_stmt.body {
                    Self::check_stmt(s, violations, file_path);
                }
            }
            Stmt::Try(try_stmt) => {
                for s in &try_stmt.body {
                    Self::check_stmt(s, violations, file_path);
                }
                for handler in &try_stmt.handlers {
                    if let rustpython_ast::ExceptHandler::ExceptHandler(h) = handler {
                        for s in &h.body {
                            Self::check_stmt(s, violations, file_path);
                        }
                    }
                }
            }
            _ => {}
        }
    }
}

impl LintRule for NoOsEnvironRule {
    fn rule_id(&self) -> &str {
        "DOEFF004"
    }

    fn description(&self) -> &str {
        "Forbid direct access to environment variables"
    }

    fn check(&self, context: &RuleContext) -> Vec<Violation> {
        let mut violations = Vec::new();
        Self::check_stmt(context.stmt, &mut violations, context.file_path);
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
        let rule = NoOsEnvironRule::new();
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
    fn test_os_environ_subscript() {
        let code = r#"
import os
api_key = os.environ["API_KEY"]
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_os_environ_get() {
        let code = r#"
import os
api_key = os.environ.get("API_KEY")
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_os_getenv() {
        let code = r#"
import os
api_key = os.getenv("API_KEY")
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_no_environ_access() {
        let code = r#"
def get_config(api_key: str) -> dict:
    return {"api_key": api_key}
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }
}




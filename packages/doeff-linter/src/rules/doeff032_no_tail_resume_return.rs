//! DOEFF032: Prefer Transfer for tail-position Resume
//!
//! `return (yield Resume(k, value))` keeps the handler generator alive until the resumed
//! continuation returns. In tail position, `yield Transfer(k, value)` is explicit and lets the VM
//! abandon the handler frame immediately.

use crate::models::{RuleContext, Severity, Violation};
use crate::rules::base::LintRule;
use rustpython_ast::{Expr, Stmt};

pub struct NoTailResumeReturnRule;

impl NoTailResumeReturnRule {
    pub fn new() -> Self {
        Self
    }

    fn is_resume_call(expr: &Expr) -> bool {
        let Expr::Call(call) = expr else {
            return false;
        };

        match &*call.func {
            Expr::Name(name) => name.id.as_str() == "Resume",
            Expr::Attribute(attr) => attr.attr.as_str() == "Resume",
            _ => false,
        }
    }

    fn is_tail_resume_return(stmt: &Stmt) -> Option<usize> {
        let Stmt::Return(return_stmt) = stmt else {
            return None;
        };
        let value = return_stmt.value.as_ref()?;
        let Expr::Yield(yield_expr) = &**value else {
            return None;
        };
        let yielded = yield_expr.value.as_ref()?;
        Self::is_resume_call(yielded).then(|| return_stmt.range.start().to_usize())
    }

    fn check_stmt(stmt: &Stmt, violations: &mut Vec<Violation>, file_path: &str) {
        if let Some(offset) = Self::is_tail_resume_return(stmt) {
            violations.push(Violation::new(
                "DOEFF032".to_string(),
                "\
`return (yield Resume(k, value))` keeps the handler frame alive while continuation `k` runs.\n\
In tail position, prefer `yield Transfer(k, value)` so the handler is abandoned explicitly.\n\
If you intentionally need post-resume processing, keep `Resume`; otherwise replace the tail \
`Resume` with `Transfer`."
                    .to_string(),
                offset,
                file_path.to_string(),
                Severity::Warning,
            ));
        }

        match stmt {
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
            Stmt::If(if_stmt) => {
                for s in &if_stmt.body {
                    Self::check_stmt(s, violations, file_path);
                }
                for s in &if_stmt.orelse {
                    Self::check_stmt(s, violations, file_path);
                }
            }
            Stmt::While(while_stmt) => {
                for s in &while_stmt.body {
                    Self::check_stmt(s, violations, file_path);
                }
                for s in &while_stmt.orelse {
                    Self::check_stmt(s, violations, file_path);
                }
            }
            Stmt::For(for_stmt) => {
                for s in &for_stmt.body {
                    Self::check_stmt(s, violations, file_path);
                }
                for s in &for_stmt.orelse {
                    Self::check_stmt(s, violations, file_path);
                }
            }
            Stmt::AsyncFor(for_stmt) => {
                for s in &for_stmt.body {
                    Self::check_stmt(s, violations, file_path);
                }
                for s in &for_stmt.orelse {
                    Self::check_stmt(s, violations, file_path);
                }
            }
            Stmt::With(with_stmt) => {
                for s in &with_stmt.body {
                    Self::check_stmt(s, violations, file_path);
                }
            }
            Stmt::AsyncWith(with_stmt) => {
                for s in &with_stmt.body {
                    Self::check_stmt(s, violations, file_path);
                }
            }
            Stmt::Try(try_stmt) => {
                for s in &try_stmt.body {
                    Self::check_stmt(s, violations, file_path);
                }
                for handler in &try_stmt.handlers {
                    let rustpython_ast::ExceptHandler::ExceptHandler(handler) = handler;
                    for s in &handler.body {
                        Self::check_stmt(s, violations, file_path);
                    }
                }
                for s in &try_stmt.orelse {
                    Self::check_stmt(s, violations, file_path);
                }
                for s in &try_stmt.finalbody {
                    Self::check_stmt(s, violations, file_path);
                }
            }
            Stmt::Match(match_stmt) => {
                for case in &match_stmt.cases {
                    for s in &case.body {
                        Self::check_stmt(s, violations, file_path);
                    }
                }
            }
            _ => {}
        }
    }
}

impl LintRule for NoTailResumeReturnRule {
    fn rule_id(&self) -> &str {
        "DOEFF032"
    }

    fn description(&self) -> &str {
        "Prefer Transfer over tail-position Resume"
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
        let rule = NoTailResumeReturnRule::new();
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
    fn test_tail_resume_return_is_flagged() {
        let code = r#"
@do
def handler(effect, k):
    return (yield Resume(k, effect.value))
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("Transfer"));
    }

    #[test]
    fn test_attribute_resume_return_is_flagged() {
        let code = r#"
@do
def handler(effect, k):
    return (yield doeff_vm.Resume(k, effect.value))
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_resume_with_post_processing_is_allowed() {
        let code = r#"
@do
def handler(effect, k):
    resumed = yield Resume(k, effect.value)
    return resumed * 3
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_transfer_is_not_flagged() {
        let code = r#"
@do
def handler(effect, k):
    yield Transfer(k, effect.value)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_nested_tail_resume_return_is_flagged() {
        let code = r#"
@do
def handler(effect, k):
    if effect.ready:
        return (yield Resume(k, effect.value))
    yield Pass()
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }
}

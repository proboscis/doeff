//! DOEFF018: No Ask Effect Inside Try-Except Blocks
//!
//! Forbid using `ask` effect inside try/except blocks. DI failures indicate
//! a programming error (missing dependency injection) and should never be
//! caught at runtime - fix the DI configuration instead.

use crate::models::{RuleContext, Severity, Violation};
use crate::rules::base::LintRule;
use rustpython_ast::{Expr, Stmt};

pub struct NoAskInTryRule;

impl NoAskInTryRule {
    pub fn new() -> Self {
        Self
    }

    /// Check if an expression is `yield ask(...)` or `yield ask(...)`
    fn is_ask_yield(expr: &Expr) -> bool {
        match expr {
            Expr::Yield(yield_expr) => {
                if let Some(value) = &yield_expr.value {
                    Self::is_ask_call(value)
                } else {
                    false
                }
            }
            _ => false,
        }
    }

    /// Check if an expression is a call to `ask(...)`
    fn is_ask_call(expr: &Expr) -> bool {
        match expr {
            Expr::Call(call) => {
                match &*call.func {
                    Expr::Name(name) => name.id.as_str() == "ask",
                    _ => false,
                }
            }
            _ => false,
        }
    }

    /// Recursively check if a statement contains `yield ask(...)`
    fn contains_ask_yield(stmt: &Stmt) -> Vec<usize> {
        let mut positions = Vec::new();
        Self::check_stmt_for_ask(stmt, &mut positions);
        positions
    }

    fn check_stmt_for_ask(stmt: &Stmt, positions: &mut Vec<usize>) {
        match stmt {
            Stmt::Expr(expr_stmt) => {
                if Self::is_ask_yield(&expr_stmt.value) {
                    positions.push(expr_stmt.range.start().to_usize());
                }
            }
            Stmt::Assign(assign) => {
                if Self::is_ask_yield(&assign.value) {
                    positions.push(assign.range.start().to_usize());
                }
            }
            Stmt::AnnAssign(ann_assign) => {
                if let Some(value) = &ann_assign.value {
                    if Self::is_ask_yield(value) {
                        positions.push(ann_assign.range.start().to_usize());
                    }
                }
            }
            Stmt::If(if_stmt) => {
                for s in &if_stmt.body {
                    Self::check_stmt_for_ask(s, positions);
                }
                for s in &if_stmt.orelse {
                    Self::check_stmt_for_ask(s, positions);
                }
            }
            Stmt::While(while_stmt) => {
                for s in &while_stmt.body {
                    Self::check_stmt_for_ask(s, positions);
                }
                for s in &while_stmt.orelse {
                    Self::check_stmt_for_ask(s, positions);
                }
            }
            Stmt::For(for_stmt) => {
                for s in &for_stmt.body {
                    Self::check_stmt_for_ask(s, positions);
                }
                for s in &for_stmt.orelse {
                    Self::check_stmt_for_ask(s, positions);
                }
            }
            Stmt::AsyncFor(for_stmt) => {
                for s in &for_stmt.body {
                    Self::check_stmt_for_ask(s, positions);
                }
                for s in &for_stmt.orelse {
                    Self::check_stmt_for_ask(s, positions);
                }
            }
            Stmt::With(with_stmt) => {
                for s in &with_stmt.body {
                    Self::check_stmt_for_ask(s, positions);
                }
            }
            Stmt::AsyncWith(with_stmt) => {
                for s in &with_stmt.body {
                    Self::check_stmt_for_ask(s, positions);
                }
            }
            Stmt::Match(match_stmt) => {
                for case in &match_stmt.cases {
                    for s in &case.body {
                        Self::check_stmt_for_ask(s, positions);
                    }
                }
            }
            // Note: We don't recurse into Try blocks here because we're already
            // checking the try block's body from check_stmt
            _ => {}
        }
    }

    fn check_stmt(stmt: &Stmt, violations: &mut Vec<Violation>, file_path: &str) {
        match stmt {
            Stmt::Try(try_stmt) => {
                // Check body for ask yields - these are violations
                for s in &try_stmt.body {
                    let positions = Self::contains_ask_yield(s);
                    for pos in positions {
                        violations.push(Violation::new(
                            "DOEFF018".to_string(),
                            "'ask' effect is used inside a try/except block. \
                             ask effect failures indicate a programming error (missing dependency injection). \
                             These should never be caught at runtime - fix the DI configuration instead. \
                             Remove the try/except and ensure the dependency is properly injected."
                                .to_string(),
                            pos,
                            file_path.to_string(),
                            Severity::Error,
                        ));
                    }
                    // Also check for nested try blocks
                    Self::check_stmt(s, violations, file_path);
                }

                // Check exception handlers for nested try blocks
                for handler in &try_stmt.handlers {
                    if let rustpython_ast::ExceptHandler::ExceptHandler(h) = handler {
                        for s in &h.body {
                            Self::check_stmt(s, violations, file_path);
                        }
                    }
                }

                // Check else clause for nested try blocks
                for s in &try_stmt.orelse {
                    Self::check_stmt(s, violations, file_path);
                }

                // Check finally clause for nested try blocks
                for s in &try_stmt.finalbody {
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

impl LintRule for NoAskInTryRule {
    fn rule_id(&self) -> &str {
        "DOEFF018"
    }

    fn description(&self) -> &str {
        "Forbid using 'ask' effect inside try/except blocks"
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
        let rule = NoAskInTryRule::new();
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
    fn test_ask_in_try_block() {
        let code = r#"
@do
def get_config():
    try:
        value = yield ask("config_key")
    except:
        value = "default"
    return value
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("ask"));
        assert!(violations[0].message.contains("programming error"));
    }

    #[test]
    fn test_ask_outside_try_block() {
        let code = r#"
@do
def get_config():
    value = yield ask("config_key")
    try:
        result = process(value)
    except ProcessError:
        result = "default"
    return result
"#;
        let violations = check_code(code);
        // Should have 0 DOEFF018 violations (ask is outside try)
        // Note: The code still uses try/except which would trigger DOEFF014
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_nested_try_with_ask() {
        let code = r#"
@do
def nested_example():
    try:
        try:
            config = yield ask("nested_config")
        except ConfigError:
            config = None
    except OuterError:
        pass
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_multiple_ask_in_try() {
        let code = r#"
@do
def multiple_asks():
    try:
        a = yield ask("config_a")
        b = yield ask("config_b")
    except:
        a = "default_a"
        b = "default_b"
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 2);
    }

    #[test]
    fn test_ask_in_try_inside_if() {
        let code = r#"
@do
def conditional_ask(flag):
    if flag:
        try:
            value = yield ask("conditional_key")
        except:
            value = None
    return value
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_no_ask_just_try() {
        let code = r#"
def regular_function():
    try:
        result = some_operation()
    except:
        result = None
"#;
        let violations = check_code(code);
        // No DOEFF018 violations - no ask calls
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_ask_in_except_handler() {
        // ask in except handler should NOT trigger DOEFF018
        // (it's not in the try body)
        let code = r#"
@do
def ask_in_except():
    try:
        result = risky_operation()
    except:
        fallback = yield ask("fallback_key")
        result = fallback
"#;
        let violations = check_code(code);
        // ask is in except handler, not in try body
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_ask_in_finally() {
        // ask in finally should NOT trigger DOEFF018
        let code = r#"
@do
def ask_in_finally():
    try:
        result = risky_operation()
    finally:
        cleanup = yield ask("cleanup_key")
"#;
        let violations = check_code(code);
        // ask is in finally, not in try body
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_ask_in_try_with_for_loop() {
        let code = r#"
@do
def ask_in_loop_in_try():
    try:
        for item in items:
            config = yield ask("item_config")
    except:
        pass
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_async_function_with_ask_in_try() {
        let code = r#"
@do
async def async_ask():
    try:
        config = yield ask("async_config")
    except TimeoutError:
        config = None
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }
}


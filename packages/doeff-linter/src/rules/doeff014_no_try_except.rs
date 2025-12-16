//! DOEFF014: Consider Effect-Based Error Handling
//!
//! Native try-except blocks work in @do functions. However, for complex error
//! handling scenarios, consider using effect-based handlers like Safe, Catch,
//! or Recover for better composability.

use crate::models::{RuleContext, Severity, Violation};
use crate::rules::base::LintRule;
use rustpython_ast::Stmt;

pub struct NoTryExceptRule;

impl NoTryExceptRule {
    pub fn new() -> Self {
        Self
    }

    fn check_stmt(stmt: &Stmt, violations: &mut Vec<Violation>, file_path: &str) {
        match stmt {
            Stmt::Try(try_stmt) => {
                // Report info on the try block - try-except now works but effects are recommended for complex cases
                violations.push(Violation::new(
                    "DOEFF014".to_string(),
                    "Native try-except works in @do functions. For complex error handling, consider \
                     effect-based alternatives: `Safe(program)` for Result object, `program.recover(fallback)` \
                     for fallbacks, `Catch(program, handler)` to transform errors. These enable better \
                     composability and explicit error flow."
                        .to_string(),
                    try_stmt.range.start().to_usize(),
                    file_path.to_string(),
                    Severity::Info,
                ));

                // Still check nested statements for other try blocks
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
                for s in &try_stmt.orelse {
                    Self::check_stmt(s, violations, file_path);
                }
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

impl LintRule for NoTryExceptRule {
    fn rule_id(&self) -> &str {
        "DOEFF014"
    }

    fn description(&self) -> &str {
        "Consider effect-based error handling (Safe, Catch, Recover) for complex cases"
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
        let rule = NoTryExceptRule::new();
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
    fn test_simple_try_except() {
        let code = r#"
def risky_operation():
    try:
        do_something()
    except Exception as e:
        handle_error(e)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("Safe"));
        assert!(violations[0].message.contains("recover"));
        assert!(violations[0].message.contains("Catch"));
    }

    #[test]
    fn test_try_except_finally() {
        let code = r#"
def with_cleanup():
    try:
        open_resource()
    except IOError:
        log_error()
    finally:
        close_resource()
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_try_except_else() {
        let code = r#"
def with_else():
    try:
        result = compute()
    except ValueError:
        result = default_value()
    else:
        process_result(result)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_multiple_except_handlers() {
        let code = r#"
def multiple_handlers():
    try:
        do_something()
    except ValueError:
        handle_value_error()
    except KeyError:
        handle_key_error()
    except Exception:
        handle_generic()
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_nested_try_except() {
        let code = r#"
def nested():
    try:
        try:
            inner_op()
        except InnerError:
            pass
    except OuterError:
        pass
"#;
        let violations = check_code(code);
        // Two violations: outer try and nested try
        assert_eq!(violations.len(), 2);
    }

    #[test]
    fn test_async_function_with_try() {
        let code = r#"
async def async_risky():
    try:
        await fetch_data()
    except TimeoutError:
        return default()
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_try_in_class_method() {
        let code = r#"
class DataProcessor:
    def process(self, data):
        try:
            return self.transform(data)
        except TransformError:
            return None
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_try_inside_if() {
        let code = r#"
def conditional_try(should_try: bool):
    if should_try:
        try:
            risky()
        except:
            pass
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_try_inside_for_loop() {
        let code = r#"
def loop_with_try(items):
    for item in items:
        try:
            process(item)
        except ItemError:
            continue
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_try_inside_while_loop() {
        let code = r#"
def while_with_try():
    while condition():
        try:
            do_something()
        except:
            break
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_try_inside_with() {
        let code = r#"
def with_context_try():
    with open("file.txt") as f:
        try:
            data = f.read()
        except IOError:
            data = ""
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_no_try_except() {
        let code = r#"
from doeff import do, Safe, Ok, Err

@do
def safe_operation():
    result = yield Safe(risky_operation())
    
    match result:
        case Ok(value):
            return value
        case Err(error):
            return default()
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_bare_except() {
        let code = r#"
def bare_except():
    try:
        risky()
    except:
        pass
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_try_in_match_case() {
        let code = r#"
def match_with_try(value):
    match value:
        case 1:
            try:
                process_one()
            except:
                pass
        case _:
            pass
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }
}



//! DOEFF019: No ask with Fallback Pattern
//!
//! Forbid using `ask` effect with fallback patterns like `arg or (yield ask(...))`.
//! The `ask` effect should be the ONLY way to obtain the value to reduce complexity.

use crate::models::{RuleContext, Severity, Violation};
use crate::rules::base::LintRule;
use rustpython_ast::{BoolOp, Expr, Stmt};

pub struct NoAskWithFallbackRule;

impl NoAskWithFallbackRule {
    pub fn new() -> Self {
        Self
    }

    /// Check if an expression is `yield ask(...)`
    fn is_yield_ask(expr: &Expr) -> bool {
        if let Expr::Yield(yield_expr) = expr {
            if let Some(value) = &yield_expr.value {
                return Self::is_ask_call(value);
            }
        }
        false
    }

    /// Check if an expression is `ask(...)` call
    fn is_ask_call(expr: &Expr) -> bool {
        if let Expr::Call(call) = expr {
            if let Expr::Name(name) = &*call.func {
                return name.id.as_str() == "ask";
            }
        }
        false
    }

    /// Check if an expression is a fallback pattern with ask
    fn is_ask_fallback_pattern(expr: &Expr) -> Option<usize> {
        match expr {
            // Pattern: arg or (yield ask(...))
            Expr::BoolOp(boolop) if matches!(boolop.op, BoolOp::Or) => {
                // Check if any value in the Or is yield ask(...)
                for value in &boolop.values {
                    if Self::is_yield_ask(value) {
                        return Some(boolop.range.start().to_usize());
                    }
                }
                None
            }
            // Pattern: arg if arg else (yield ask(...))
            Expr::IfExp(ifexp) => {
                if Self::is_yield_ask(&ifexp.orelse) {
                    return Some(ifexp.range.start().to_usize());
                }
                None
            }
            _ => None,
        }
    }

    /// Recursively check all expressions in a statement
    fn check_expr(expr: &Expr, violations: &mut Vec<Violation>, file_path: &str) {
        // Check if this expression is the fallback pattern
        if let Some(offset) = Self::is_ask_fallback_pattern(expr) {
            let message = "\
'ask' effect is used with a fallback pattern.

Problem: Using 'arg or (yield ask(...))' or 'arg if arg else (yield ask(...))' creates
ambiguity about where the value comes from. The 'ask' effect should be the ONLY way to
obtain the value to reduce complexity.

Fix: Remove the fallback and use ask as the sole source:
  # Before
  @do
  def do_something(arg=None):
      arg = arg or (yield ask(\"arg_key\"))
  
  # After
  @do
  def do_something():
      arg = yield ask(\"arg_key\")  # Single source of truth";

            violations.push(Violation::new(
                "DOEFF019".to_string(),
                message.to_string(),
                offset,
                file_path.to_string(),
                Severity::Warning,
            ));
        }

        // Recursively check nested expressions
        Self::check_nested_exprs(expr, violations, file_path);
    }

    /// Check nested expressions within an expression
    fn check_nested_exprs(expr: &Expr, violations: &mut Vec<Violation>, file_path: &str) {
        match expr {
            Expr::BoolOp(boolop) => {
                for value in &boolop.values {
                    Self::check_expr(value, violations, file_path);
                }
            }
            Expr::NamedExpr(named) => {
                Self::check_expr(&named.value, violations, file_path);
            }
            Expr::BinOp(binop) => {
                Self::check_expr(&binop.left, violations, file_path);
                Self::check_expr(&binop.right, violations, file_path);
            }
            Expr::UnaryOp(unaryop) => {
                Self::check_expr(&unaryop.operand, violations, file_path);
            }
            Expr::Lambda(lambda) => {
                Self::check_expr(&lambda.body, violations, file_path);
            }
            Expr::IfExp(ifexp) => {
                Self::check_expr(&ifexp.test, violations, file_path);
                Self::check_expr(&ifexp.body, violations, file_path);
                Self::check_expr(&ifexp.orelse, violations, file_path);
            }
            Expr::Dict(dict) => {
                for key in dict.keys.iter().flatten() {
                    Self::check_expr(key, violations, file_path);
                }
                for value in &dict.values {
                    Self::check_expr(value, violations, file_path);
                }
            }
            Expr::Set(set) => {
                for elt in &set.elts {
                    Self::check_expr(elt, violations, file_path);
                }
            }
            Expr::ListComp(listcomp) => {
                Self::check_expr(&listcomp.elt, violations, file_path);
            }
            Expr::SetComp(setcomp) => {
                Self::check_expr(&setcomp.elt, violations, file_path);
            }
            Expr::DictComp(dictcomp) => {
                Self::check_expr(&dictcomp.key, violations, file_path);
                Self::check_expr(&dictcomp.value, violations, file_path);
            }
            Expr::GeneratorExp(genexp) => {
                Self::check_expr(&genexp.elt, violations, file_path);
            }
            Expr::Await(await_expr) => {
                Self::check_expr(&await_expr.value, violations, file_path);
            }
            Expr::Yield(yield_expr) => {
                if let Some(value) = &yield_expr.value {
                    Self::check_expr(value, violations, file_path);
                }
            }
            Expr::YieldFrom(yieldfrom) => {
                Self::check_expr(&yieldfrom.value, violations, file_path);
            }
            Expr::Compare(compare) => {
                Self::check_expr(&compare.left, violations, file_path);
                for comp in &compare.comparators {
                    Self::check_expr(comp, violations, file_path);
                }
            }
            Expr::Call(call) => {
                Self::check_expr(&call.func, violations, file_path);
                for arg in &call.args {
                    Self::check_expr(arg, violations, file_path);
                }
                for keyword in &call.keywords {
                    Self::check_expr(&keyword.value, violations, file_path);
                }
            }
            Expr::FormattedValue(fv) => {
                Self::check_expr(&fv.value, violations, file_path);
            }
            Expr::JoinedStr(js) => {
                for value in &js.values {
                    Self::check_expr(value, violations, file_path);
                }
            }
            Expr::Attribute(attr) => {
                Self::check_expr(&attr.value, violations, file_path);
            }
            Expr::Subscript(subscript) => {
                Self::check_expr(&subscript.value, violations, file_path);
                Self::check_expr(&subscript.slice, violations, file_path);
            }
            Expr::Starred(starred) => {
                Self::check_expr(&starred.value, violations, file_path);
            }
            Expr::List(list) => {
                for elt in &list.elts {
                    Self::check_expr(elt, violations, file_path);
                }
            }
            Expr::Tuple(tuple) => {
                for elt in &tuple.elts {
                    Self::check_expr(elt, violations, file_path);
                }
            }
            Expr::Slice(slice) => {
                if let Some(lower) = &slice.lower {
                    Self::check_expr(lower, violations, file_path);
                }
                if let Some(upper) = &slice.upper {
                    Self::check_expr(upper, violations, file_path);
                }
                if let Some(step) = &slice.step {
                    Self::check_expr(step, violations, file_path);
                }
            }
            // Constants, names, etc. don't need recursive checking
            _ => {}
        }
    }

    /// Check all statements recursively
    fn check_stmt(stmt: &Stmt, violations: &mut Vec<Violation>, file_path: &str) {
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
            Stmt::Return(ret) => {
                if let Some(value) = &ret.value {
                    Self::check_expr(value, violations, file_path);
                }
            }
            Stmt::Assign(assign) => {
                Self::check_expr(&assign.value, violations, file_path);
            }
            Stmt::AnnAssign(ann_assign) => {
                if let Some(value) = &ann_assign.value {
                    Self::check_expr(value, violations, file_path);
                }
            }
            Stmt::AugAssign(aug_assign) => {
                Self::check_expr(&aug_assign.value, violations, file_path);
            }
            Stmt::For(for_stmt) => {
                Self::check_expr(&for_stmt.iter, violations, file_path);
                for s in &for_stmt.body {
                    Self::check_stmt(s, violations, file_path);
                }
                for s in &for_stmt.orelse {
                    Self::check_stmt(s, violations, file_path);
                }
            }
            Stmt::AsyncFor(for_stmt) => {
                Self::check_expr(&for_stmt.iter, violations, file_path);
                for s in &for_stmt.body {
                    Self::check_stmt(s, violations, file_path);
                }
                for s in &for_stmt.orelse {
                    Self::check_stmt(s, violations, file_path);
                }
            }
            Stmt::While(while_stmt) => {
                Self::check_expr(&while_stmt.test, violations, file_path);
                for s in &while_stmt.body {
                    Self::check_stmt(s, violations, file_path);
                }
                for s in &while_stmt.orelse {
                    Self::check_stmt(s, violations, file_path);
                }
            }
            Stmt::If(if_stmt) => {
                Self::check_expr(&if_stmt.test, violations, file_path);
                for s in &if_stmt.body {
                    Self::check_stmt(s, violations, file_path);
                }
                for s in &if_stmt.orelse {
                    Self::check_stmt(s, violations, file_path);
                }
            }
            Stmt::With(with_stmt) => {
                for item in &with_stmt.items {
                    Self::check_expr(&item.context_expr, violations, file_path);
                }
                for s in &with_stmt.body {
                    Self::check_stmt(s, violations, file_path);
                }
            }
            Stmt::AsyncWith(with_stmt) => {
                for item in &with_stmt.items {
                    Self::check_expr(&item.context_expr, violations, file_path);
                }
                for s in &with_stmt.body {
                    Self::check_stmt(s, violations, file_path);
                }
            }
            Stmt::Match(match_stmt) => {
                Self::check_expr(&match_stmt.subject, violations, file_path);
                for case in &match_stmt.cases {
                    if let Some(guard) = &case.guard {
                        Self::check_expr(guard, violations, file_path);
                    }
                    for s in &case.body {
                        Self::check_stmt(s, violations, file_path);
                    }
                }
            }
            Stmt::Raise(raise) => {
                if let Some(exc) = &raise.exc {
                    Self::check_expr(exc, violations, file_path);
                }
                if let Some(cause) = &raise.cause {
                    Self::check_expr(cause, violations, file_path);
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
                for s in &try_stmt.orelse {
                    Self::check_stmt(s, violations, file_path);
                }
                for s in &try_stmt.finalbody {
                    Self::check_stmt(s, violations, file_path);
                }
            }
            Stmt::Assert(assert) => {
                Self::check_expr(&assert.test, violations, file_path);
                if let Some(msg) = &assert.msg {
                    Self::check_expr(msg, violations, file_path);
                }
            }
            Stmt::Expr(expr_stmt) => {
                Self::check_expr(&expr_stmt.value, violations, file_path);
            }
            _ => {}
        }
    }
}

impl LintRule for NoAskWithFallbackRule {
    fn rule_id(&self) -> &str {
        "DOEFF019"
    }

    fn description(&self) -> &str {
        "Forbid using 'ask' effect with fallback patterns; ask should be the sole source"
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
        let rule = NoAskWithFallbackRule::new();
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
    fn test_or_fallback_pattern() {
        let code = r#"
@do
def do_something(arg=None):
    arg = arg or (yield ask("arg_key"))
    return arg
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("ask"));
        assert!(violations[0].message.contains("fallback"));
    }

    #[test]
    fn test_or_fallback_with_parentheses_on_yield() {
        // `arg or yield ask(...)` without outer parens is a syntax error in Python
        // So we test with parentheses around the yield expression
        let code = r#"
@do
def do_something(arg=None):
    arg = arg or (yield ask("arg_key"))
    return arg
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_if_else_fallback_pattern() {
        let code = r#"
@do
def do_something(arg=None):
    value = arg if arg else (yield ask("arg_key"))
    return value
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("ask"));
    }

    #[test]
    fn test_ternary_with_ask_in_else() {
        let code = r#"
@do
def get_config(override=None):
    config = override if override is not None else (yield ask("config"))
    return config
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_standalone_ask_allowed() {
        let code = r#"
@do
def do_something():
    arg = yield ask("arg_key")
    return arg
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_ask_in_function_body_allowed() {
        let code = r#"
@do
def get_config():
    config = yield ask("config")
    value = yield ask("value")
    return config + value
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_multiple_fallbacks() {
        let code = r#"
@do
def get_values(a=None, b=None):
    a = a or (yield ask("a"))
    b = b or (yield ask("b"))
    return a + b
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 2);
    }

    #[test]
    fn test_nested_or_pattern() {
        let code = r#"
@do
def complex_fallback(x=None, y=None):
    result = x or y or (yield ask("key"))
    return result
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_regular_or_without_ask() {
        let code = r#"
def simple_or(a, b):
    return a or b
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_regular_ternary_without_ask() {
        let code = r#"
def simple_ternary(x):
    return x if x else "default"
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_ask_in_if_body_allowed() {
        let code = r#"
@do
def conditional_ask(should_ask):
    if should_ask:
        value = yield ask("key")
    else:
        value = "default"
    return value
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_nested_function_with_fallback() {
        let code = r#"
def outer():
    @do
    def inner(arg=None):
        arg = arg or (yield ask("arg"))
        return arg
    return inner
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_async_function_with_fallback() {
        let code = r#"
@do
async def async_do(arg=None):
    arg = arg or (yield ask("arg"))
    return arg
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }
}


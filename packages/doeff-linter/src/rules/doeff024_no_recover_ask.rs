//! DOEFF024: No recover with ask Effect
//!
//! Forbid using `recover` with `ask` effect.
//! The `ask` effect should fail fast to help users identify missing dependencies.

use crate::models::{RuleContext, Severity, Violation};
use crate::rules::base::LintRule;
use rustpython_ast::{Expr, Stmt};

pub struct NoRecoverAskRule;

impl NoRecoverAskRule {
    pub fn new() -> Self {
        Self
    }

    /// Recursively check if an expression contains an `ask` call
    fn contains_ask_call(expr: &Expr) -> bool {
        match expr {
            Expr::Call(call) => {
                // Check if this is ask(...)
                if let Expr::Name(name) = &*call.func {
                    if name.id.as_str() == "ask" {
                        return true;
                    }
                }
                // Check func and all arguments
                if Self::contains_ask_call(&call.func) {
                    return true;
                }
                for arg in &call.args {
                    if Self::contains_ask_call(arg) {
                        return true;
                    }
                }
                for keyword in &call.keywords {
                    if Self::contains_ask_call(&keyword.value) {
                        return true;
                    }
                }
                false
            }
            Expr::BoolOp(boolop) => boolop.values.iter().any(Self::contains_ask_call),
            Expr::NamedExpr(named) => Self::contains_ask_call(&named.value),
            Expr::BinOp(binop) => {
                Self::contains_ask_call(&binop.left) || Self::contains_ask_call(&binop.right)
            }
            Expr::UnaryOp(unaryop) => Self::contains_ask_call(&unaryop.operand),
            Expr::Lambda(lambda) => Self::contains_ask_call(&lambda.body),
            Expr::IfExp(ifexp) => {
                Self::contains_ask_call(&ifexp.test)
                    || Self::contains_ask_call(&ifexp.body)
                    || Self::contains_ask_call(&ifexp.orelse)
            }
            Expr::Dict(dict) => {
                dict.keys.iter().flatten().any(Self::contains_ask_call)
                    || dict.values.iter().any(Self::contains_ask_call)
            }
            Expr::Set(set) => set.elts.iter().any(Self::contains_ask_call),
            Expr::ListComp(listcomp) => Self::contains_ask_call(&listcomp.elt),
            Expr::SetComp(setcomp) => Self::contains_ask_call(&setcomp.elt),
            Expr::DictComp(dictcomp) => {
                Self::contains_ask_call(&dictcomp.key) || Self::contains_ask_call(&dictcomp.value)
            }
            Expr::GeneratorExp(genexp) => Self::contains_ask_call(&genexp.elt),
            Expr::Await(await_expr) => Self::contains_ask_call(&await_expr.value),
            Expr::Yield(yield_expr) => yield_expr
                .value
                .as_ref()
                .map_or(false, |v| Self::contains_ask_call(v)),
            Expr::YieldFrom(yieldfrom) => Self::contains_ask_call(&yieldfrom.value),
            Expr::Compare(compare) => {
                Self::contains_ask_call(&compare.left)
                    || compare.comparators.iter().any(Self::contains_ask_call)
            }
            Expr::FormattedValue(fv) => Self::contains_ask_call(&fv.value),
            Expr::JoinedStr(js) => js.values.iter().any(Self::contains_ask_call),
            Expr::Attribute(attr) => Self::contains_ask_call(&attr.value),
            Expr::Subscript(subscript) => {
                Self::contains_ask_call(&subscript.value)
                    || Self::contains_ask_call(&subscript.slice)
            }
            Expr::Starred(starred) => Self::contains_ask_call(&starred.value),
            Expr::List(list) => list.elts.iter().any(Self::contains_ask_call),
            Expr::Tuple(tuple) => tuple.elts.iter().any(Self::contains_ask_call),
            Expr::Slice(slice) => {
                slice
                    .lower
                    .as_ref()
                    .map_or(false, |v| Self::contains_ask_call(v))
                    || slice
                        .upper
                        .as_ref()
                        .map_or(false, |v| Self::contains_ask_call(v))
                    || slice
                        .step
                        .as_ref()
                        .map_or(false, |v| Self::contains_ask_call(v))
            }
            _ => false,
        }
    }

    /// Check if an expression is a `recover(ask(...), ...)` or `Recover(ask(...), ...)` call
    fn is_recover_with_ask(expr: &Expr) -> Option<usize> {
        if let Expr::Call(call) = expr {
            if let Expr::Name(name) = &*call.func {
                let func_name = name.id.as_str();
                if func_name == "recover" || func_name == "Recover" {
                    // Check the first positional argument (sub_program)
                    if let Some(first_arg) = call.args.first() {
                        if Self::contains_ask_call(first_arg) {
                            return Some(call.range.start().to_usize());
                        }
                    }
                    // Also check if sub_program is passed as a keyword argument
                    for keyword in &call.keywords {
                        if let Some(arg_name) = &keyword.arg {
                            if arg_name.as_str() == "sub_program" {
                                if Self::contains_ask_call(&keyword.value) {
                                    return Some(call.range.start().to_usize());
                                }
                            }
                        }
                    }
                }
            }
        }
        None
    }

    /// Recursively check all expressions in a statement
    fn check_expr(expr: &Expr, violations: &mut Vec<Violation>, file_path: &str) {
        // Check if this expression is recover(ask(...), ...)
        if let Some(offset) = Self::is_recover_with_ask(expr) {
            let message = "\
'recover' is used with 'ask' effect.

Problem: The 'ask' effect is designed to fail fast when a required dependency is missing.
Wrapping 'ask' in 'recover' defeats this purpose by silently falling back to a default,
which can hide configuration errors and make debugging harder.

Fix: Remove the recover wrapper and let ask fail if the dependency is missing:
  # Before
  @do
  def get_impl():
      impl = yield recover(ask(\"impl_key\"), fallback=default_impl)
      return impl
  
  # After
  @do
  def get_impl():
      impl = yield ask(\"impl_key\")  # Fails fast if not provided
      return impl

If you truly need a fallback, provide the default in the environment instead:
  run_program(get_impl(), env={\"impl_key\": default_impl})";

            violations.push(Violation::new(
                "DOEFF024".to_string(),
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

impl LintRule for NoRecoverAskRule {
    fn rule_id(&self) -> &str {
        "DOEFF024"
    }

    fn description(&self) -> &str {
        "Forbid using 'recover' with 'ask' effect; ask should fail fast"
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
        let rule = NoRecoverAskRule::new();
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
    fn test_recover_with_ask_direct() {
        let code = r#"
@do
def get_impl():
    impl = yield recover(ask("impl_key"), fallback=default_impl)
    return impl
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("recover"));
        assert!(violations[0].message.contains("ask"));
    }

    #[test]
    fn test_recover_uppercase_with_ask() {
        let code = r#"
@do
def get_impl():
    impl = yield Recover(ask("impl_key"), fallback=default_impl)
    return impl
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_recover_with_ask_and_lambda_fallback() {
        let code = r#"
@do
def get_impl():
    impl = yield recover(
        ask(IMPL_KEY),
        fallback=lambda _: default_impl,
    )
    return impl
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_recover_with_non_ask_allowed() {
        let code = r#"
@do
def do_something():
    result = yield recover(
        some_dangerous_operation(),
        fallback=default_value,
    )
    return result
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_standalone_ask_allowed() {
        let code = r#"
@do
def get_value():
    value = yield ask("key")
    return value
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_recover_with_nested_ask() {
        // Even if ask is nested in another call, it should be detected
        let code = r#"
@do
def get_impl():
    impl = yield recover(some_wrapper(ask("key")), fallback=default)
    return impl
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_multiple_recover_with_ask() {
        let code = r#"
@do
def get_values():
    a = yield recover(ask("a"), fallback="default_a")
    b = yield recover(ask("b"), fallback="default_b")
    return a, b
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 2);
    }

    #[test]
    fn test_recover_without_ask_in_first_arg() {
        let code = r#"
@do
def do_something():
    # ask is in fallback, not in sub_program - this is fine
    result = yield recover(
        dangerous_op(),
        fallback=lambda _: (yield ask("fallback_key")),
    )
    return result
"#;
        let violations = check_code(code);
        // The ask is in the fallback, not the sub_program, so no violation
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_other_functions_with_ask_allowed() {
        let code = r#"
@do
def do_something():
    # Using ask with other functions is fine
    result = yield safe(ask("key"))
    return result
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_nested_function_with_recover_ask() {
        let code = r#"
def outer():
    @do
    def inner():
        impl = yield recover(ask("key"), fallback=default)
        return impl
    return inner
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_async_function_with_recover_ask() {
        let code = r#"
@do
async def async_get():
    impl = yield recover(ask("key"), fallback=default)
    return impl
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_recover_with_keyword_sub_program() {
        let code = r#"
@do
def get_impl():
    impl = yield recover(sub_program=ask("key"), fallback=default)
    return impl
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }
}

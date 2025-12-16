//! DOEFF031: No Redundant @do Wrapper Entrypoints
//!
//! Detects Program entrypoints that are created by calling a @do function that does nothing
//! except forward its arguments to a single yielded call and return the result.
//!
//! In these cases, the Program can often be created directly by calling the underlying program:
//!
//! ```python
//! # Before
//! @do
//! def _wrapper(x: int) -> EffectGenerator[int]:
//!     result: int = yield run(x=x)
//!     return result
//!
//! p_run: Program[int] = _wrapper(x=1)
//!
//! # After
//! p_run: Program[int] = run(x=1)
//! ```

use crate::models::{RuleContext, Severity, Violation};
use crate::rules::base::LintRule;
use rustpython_ast::{Expr, Mod, Stmt, StmtAsyncFunctionDef, StmtFunctionDef};
use std::collections::{HashMap, HashSet};

pub struct NoRedundantDoWrapperEntrypointRule;

struct TrivialWrapperInfo {
    underlying_call: String,
}

impl NoRedundantDoWrapperEntrypointRule {
    pub fn new() -> Self {
        Self
    }

    fn is_program_type(expr: &Expr) -> bool {
        match expr {
            Expr::Name(name) => name.id.as_str() == "Program",
            Expr::Subscript(subscript) => {
                if let Expr::Name(name) = &*subscript.value {
                    name.id.as_str() == "Program"
                } else {
                    false
                }
            }
            Expr::BinOp(binop) => Self::is_program_type(&binop.left) || Self::is_program_type(&binop.right),
            _ => false,
        }
    }

    fn has_do_decorator(decorators: &[Expr]) -> bool {
        for dec in decorators {
            match dec {
                Expr::Name(name) if name.id.as_str() == "do" => return true,
                Expr::Call(call) => {
                    if let Expr::Name(name) = &*call.func {
                        if name.id.as_str() == "do" {
                            return true;
                        }
                    }
                }
                _ => {}
            }
        }
        false
    }

    fn is_docstring_stmt(stmt: &Stmt) -> bool {
        if let Stmt::Expr(expr_stmt) = stmt {
            if let Expr::Constant(constant) = &*expr_stmt.value {
                return constant.value.is_str();
            }
        }
        false
    }

    fn expr_to_string(expr: &Expr) -> String {
        match expr {
            Expr::Name(name) => name.id.to_string(),
            Expr::Attribute(attr) => {
                let base = Self::expr_to_string(&attr.value);
                format!("{}.{}", base, attr.attr)
            }
            _ => "<expr>".to_string(),
        }
    }

    fn get_target_name(expr: &Expr) -> Option<String> {
        match expr {
            Expr::Name(name) => Some(name.id.to_string()),
            _ => None,
        }
    }

    fn collect_param_names(args: &rustpython_ast::Arguments) -> HashSet<String> {
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
        if let Some(vararg) = &args.vararg {
            names.insert(vararg.arg.to_string());
        }
        if let Some(kwarg) = &args.kwarg {
            names.insert(kwarg.arg.to_string());
        }
        names
    }

    fn is_direct_param_forwarding_call(
        call: &rustpython_ast::ExprCall,
        param_names: &HashSet<String>,
    ) -> bool {
        if !call.args.is_empty() {
            return false;
        }

        for kw in &call.keywords {
            let Some(arg_name) = &kw.arg else {
                // **kwargs
                return false;
            };

            let arg_name = arg_name.to_string();
            if !param_names.contains(&arg_name) {
                return false;
            }

            match &kw.value {
                Expr::Name(value_name) if value_name.id.as_str() == arg_name.as_str() => {}
                _ => return false,
            }
        }

        true
    }

    fn get_trivial_yielded_call(
        expr: &Expr,
        param_names: &HashSet<String>,
    ) -> Option<String> {
        let Expr::Yield(yield_expr) = expr else {
            return None;
        };
        let Some(value) = &yield_expr.value else {
            return None;
        };
        let Expr::Call(call) = &**value else {
            return None;
        };

        if !Self::is_direct_param_forwarding_call(call, param_names) {
            return None;
        }

        Some(Self::expr_to_string(&call.func))
    }

    fn parse_trivial_wrapper_body(
        body: &[Stmt],
        param_names: &HashSet<String>,
    ) -> Option<String> {
        let start = body.first().is_some_and(Self::is_docstring_stmt) as usize;
        let body = &body[start..];

        if body.len() == 1 {
            if let Stmt::Return(ret) = &body[0] {
                if let Some(value) = &ret.value {
                    return Self::get_trivial_yielded_call(value, param_names);
                }
            }
            return None;
        }

        if body.len() != 2 {
            return None;
        }

        let (assigned_name, assigned_value): (String, &Expr) = match &body[0] {
            Stmt::Assign(assign) => {
                if assign.targets.len() != 1 {
                    return None;
                }
                let Expr::Name(name) = &assign.targets[0] else {
                    return None;
                };
                (name.id.to_string(), assign.value.as_ref())
            }
            Stmt::AnnAssign(ann_assign) => {
                let Expr::Name(name) = &*ann_assign.target else {
                    return None;
                };
                let Some(value) = &ann_assign.value else {
                    return None;
                };
                (name.id.to_string(), value.as_ref())
            }
            _ => return None,
        };

        let Stmt::Return(ret) = &body[1] else {
            return None;
        };
        let Some(ret_value) = &ret.value else {
            return None;
        };
        let Expr::Name(ret_name) = &**ret_value else {
            return None;
        };
        if ret_name.id.as_str() != assigned_name.as_str() {
            return None;
        }

        Self::get_trivial_yielded_call(assigned_value, param_names)
    }

    fn collect_trivial_do_wrappers(ast: &Mod) -> HashMap<String, TrivialWrapperInfo> {
        let mut wrappers = HashMap::new();

        let Mod::Module(module) = ast else {
            return wrappers;
        };

        for stmt in &module.body {
            match stmt {
                Stmt::FunctionDef(func) if Self::has_do_decorator(&func.decorator_list) => {
                    Self::collect_trivial_wrapper_from_function(func, &mut wrappers);
                }
                Stmt::AsyncFunctionDef(func) if Self::has_do_decorator(&func.decorator_list) => {
                    Self::collect_trivial_wrapper_from_async_function(func, &mut wrappers);
                }
                _ => {}
            }
        }

        wrappers
    }

    fn collect_trivial_wrapper_from_function(
        func: &StmtFunctionDef,
        wrappers: &mut HashMap<String, TrivialWrapperInfo>,
    ) {
        let param_names = Self::collect_param_names(&func.args);
        let Some(underlying_call) = Self::parse_trivial_wrapper_body(&func.body, &param_names) else {
            return;
        };
        wrappers.insert(
            func.name.to_string(),
            TrivialWrapperInfo { underlying_call },
        );
    }

    fn collect_trivial_wrapper_from_async_function(
        func: &StmtAsyncFunctionDef,
        wrappers: &mut HashMap<String, TrivialWrapperInfo>,
    ) {
        let param_names = Self::collect_param_names(&func.args);
        let Some(underlying_call) = Self::parse_trivial_wrapper_body(&func.body, &param_names) else {
            return;
        };
        wrappers.insert(
            func.name.to_string(),
            TrivialWrapperInfo { underlying_call },
        );
    }

    fn is_keyword_only_call(call: &rustpython_ast::ExprCall) -> bool {
        if !call.args.is_empty() {
            return false;
        }
        call.keywords.iter().all(|kw| kw.arg.is_some())
    }
}

impl LintRule for NoRedundantDoWrapperEntrypointRule {
    fn rule_id(&self) -> &str {
        "DOEFF031"
    }

    fn description(&self) -> &str {
        "Program entrypoints should not use redundant @do wrappers"
    }

    fn check(&self, context: &RuleContext) -> Vec<Violation> {
        let mut violations = Vec::new();

        let Stmt::AnnAssign(ann_assign) = context.stmt else {
            return violations;
        };

        if !Self::is_program_type(&ann_assign.annotation) {
            return violations;
        }

        let Some(value) = &ann_assign.value else {
            return violations;
        };
        let Expr::Call(entry_call) = &**value else {
            return violations;
        };

        if !Self::is_keyword_only_call(entry_call) {
            return violations;
        }

        let Expr::Name(entry_func) = &*entry_call.func else {
            return violations;
        };
        let wrapper_name = entry_func.id.to_string();

        let wrappers = Self::collect_trivial_do_wrappers(context.ast);
        let Some(wrapper) = wrappers.get(&wrapper_name) else {
            return violations;
        };

        let var_name = Self::get_target_name(&ann_assign.target).unwrap_or_else(|| "<unknown>".to_string());
        let message = format!(
            "Program entrypoint '{}' is created by calling redundant @do wrapper '{}'.\n\n\
Problem: '{}' only yields '{}' with direct parameter forwarding and returns the result. \
This adds boilerplate and hides the actual entrypoint program.\n\n\
Fix: Call the underlying program directly (same arguments):\n  \
# Before\n  \
{}: Program[...] = {}(...)\n\n  \
# After\n  \
{}: Program[...] = {}(...)\n\n\
If the wrapper exists intentionally (naming/tracing/doc), suppress with: # noqa: {}",
            var_name,
            wrapper_name,
            wrapper_name,
            wrapper.underlying_call,
            var_name,
            wrapper_name,
            var_name,
            wrapper.underlying_call,
            self.rule_id()
        );

        violations.push(Violation::new(
            self.rule_id().to_string(),
            message,
            ann_assign.range.start().to_usize(),
            context.file_path.to_string(),
            Severity::Info,
        ));

        violations
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rustpython_parser::{parse, Mode};

    fn check_code(code: &str) -> Vec<Violation> {
        let ast = parse(code, Mode::Module, "test.py").unwrap();
        let rule = NoRedundantDoWrapperEntrypointRule::new();
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
    fn test_redundant_wrapper_entrypoint_triggers_assign_return() {
        let code = r#"
@do
def _wrapper(text: str, max_lines: int) -> EffectGenerator[int]:
    result: int = yield optimize(text=text, max_lines=max_lines)
    return result

p_opt: Program[int] = _wrapper(text="hi", max_lines=3)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("p_opt"));
        assert!(violations[0].message.contains("_wrapper"));
        assert!(violations[0].message.contains("optimize"));
        assert_eq!(violations[0].severity, Severity::Info);
    }

    #[test]
    fn test_redundant_wrapper_entrypoint_triggers_direct_return_yield() {
        let code = r#"
@do
def _wrapper(x: int) -> EffectGenerator[int]:
    return (yield optimize(x=x))

p_opt: Program[int] = _wrapper(x=1)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("optimize"));
    }

    #[test]
    fn test_redundant_wrapper_entrypoint_triggers_plain_assign() {
        let code = r#"
@do
def _wrapper(x: int) -> EffectGenerator[int]:
    result = yield optimize(x=x)
    return result

p_opt: Program[int] = _wrapper(x=1)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("_wrapper"));
    }

    #[test]
    fn test_no_violation_when_extra_yield_present() {
        let code = r#"
@do
def _wrapper(x: int) -> EffectGenerator[int]:
    yield slog("start")
    return (yield optimize(x=x))

p_opt: Program[int] = _wrapper(x=1)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_no_violation_when_args_not_direct_forward() {
        let code = r#"
@do
def _wrapper(x: int) -> EffectGenerator[int]:
    return (yield optimize(x=x + 1))

p_opt: Program[int] = _wrapper(x=1)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_no_violation_when_entrypoint_uses_positional_args() {
        let code = r#"
@do
def _wrapper(x: int) -> EffectGenerator[int]:
    return (yield optimize(x=x))

p_opt: Program[int] = _wrapper(1)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }
}

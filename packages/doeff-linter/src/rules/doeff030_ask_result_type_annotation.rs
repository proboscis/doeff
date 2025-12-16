//! DOEFF030: Ask Result Must Be Type Annotated
//!
//! The value yielded from `ask(...)` should be bound to a variable with an inline
//! type annotation:
//!
//! ```python
//! some_flag: bool = yield ask("project.a.use_b")
//! ```
//!
//! When requesting a callable/KleisliProgram via `ask`, use a Protocol as the key
//! and annotation, and ensure there is a corresponding `@impl(Protocol)` provider:
//!
//! ```python
//! from typing import Protocol
//!
//! class UploadFunc(Protocol):
//!     def __call__(self, bin): ...
//!
//! uploader: UploadFunc = yield ask(UploadFunc)
//!
//! @impl(UploadFunc)
//! def upload_gcs(bin): ...
//! ```

use crate::models::{RuleContext, Severity, Violation};
use crate::rules::base::LintRule;
use rustpython_ast::{Expr, Mod, Stmt};
use std::collections::HashSet;

pub struct AskResultTypeAnnotationRule;

impl AskResultTypeAnnotationRule {
    pub fn new() -> Self {
        Self
    }

    /// Return the underlying ask(...) call if expression is `yield ask(...)`.
    fn get_yield_ask_call(expr: &Expr) -> Option<&rustpython_ast::ExprCall> {
        if let Expr::Yield(yield_expr) = expr {
            if let Some(value) = &yield_expr.value {
                if let Expr::Call(call) = &**value {
                    if let Expr::Name(name) = &*call.func {
                        if name.id.as_str() == "ask" {
                            return Some(call);
                        }
                    }
                }
            }
        }
        None
    }

    fn get_ask_key_expr(call: &rustpython_ast::ExprCall) -> Option<&Expr> {
        if let Some(first) = call.args.first() {
            return Some(first);
        }
        for kw in &call.keywords {
            if let Some(arg_name) = &kw.arg {
                if arg_name.as_str() == "key" {
                    return Some(&kw.value);
                }
            }
        }
        None
    }

    /// Extract the last identifier from a Name/Attribute chain.
    fn last_name(expr: &Expr) -> Option<String> {
        match expr {
            Expr::Name(name) => Some(name.id.to_string()),
            Expr::Attribute(attr) => Some(attr.attr.to_string()),
            _ => None,
        }
    }

    fn looks_like_type_name(name: &str) -> bool {
        let first_upper = name.chars().next().map(|c| c.is_uppercase()).unwrap_or(false);
        if !first_upper {
            return false;
        }
        // Exclude SCREAMING_SNAKE_CASE constants used as string keys.
        name.chars().any(|c| c.is_lowercase())
    }

    /// Check if an expression contains a given Name/Attribute identifier.
    fn expr_contains_name(expr: &Expr, target: &str) -> bool {
        match expr {
            Expr::Name(name) => name.id.as_str() == target,
            Expr::Attribute(attr) => attr.attr.as_str() == target,
            Expr::Subscript(sub) => {
                Self::expr_contains_name(&sub.value, target)
                    || Self::expr_contains_name(&sub.slice, target)
            }
            Expr::BinOp(binop) => {
                Self::expr_contains_name(&binop.left, target)
                    || Self::expr_contains_name(&binop.right, target)
            }
            Expr::Tuple(t) => t.elts.iter().any(|e| Self::expr_contains_name(e, target)),
            Expr::List(l) => l.elts.iter().any(|e| Self::expr_contains_name(e, target)),
            Expr::BoolOp(b) => b.values.iter().any(|e| Self::expr_contains_name(e, target)),
            Expr::IfExp(ifexp) => {
                Self::expr_contains_name(&ifexp.body, target)
                    || Self::expr_contains_name(&ifexp.orelse, target)
                    || Self::expr_contains_name(&ifexp.test, target)
            }
            _ => false,
        }
    }

    fn is_protocol_base(expr: &Expr) -> bool {
        match expr {
            Expr::Name(name) => name.id.as_str() == "Protocol",
            Expr::Attribute(attr) => attr.attr.as_str() == "Protocol",
            _ => false,
        }
    }

    fn collect_local_classes(ast: &Mod) -> HashSet<String> {
        let mut classes = HashSet::new();
        if let Mod::Module(module) = ast {
            for stmt in &module.body {
                if let Stmt::ClassDef(class_def) = stmt {
                    classes.insert(class_def.name.to_string());
                }
            }
        }
        classes
    }

    fn collect_protocols(ast: &Mod) -> HashSet<String> {
        let mut protocols = HashSet::new();
        if let Mod::Module(module) = ast {
            for stmt in &module.body {
                if let Stmt::ClassDef(class_def) = stmt {
                    if class_def.bases.iter().any(Self::is_protocol_base) {
                        protocols.insert(class_def.name.to_string());
                    }
                }
            }
        }
        protocols
    }

    fn collect_impl_protocols(ast: &Mod) -> HashSet<String> {
        let mut impl_protocols = HashSet::new();
        if let Mod::Module(module) = ast {
            for stmt in &module.body {
                match stmt {
                    Stmt::FunctionDef(func) => {
                        Self::collect_impl_from_decorators(&func.decorator_list, &mut impl_protocols);
                    }
                    Stmt::AsyncFunctionDef(func) => {
                        Self::collect_impl_from_decorators(&func.decorator_list, &mut impl_protocols);
                    }
                    _ => {}
                }
            }
        }
        impl_protocols
    }

    fn collect_impl_from_decorators(decorators: &[Expr], out: &mut HashSet<String>) {
        for dec in decorators {
            if let Expr::Call(call) = dec {
                if let Expr::Name(name) = &*call.func {
                    if name.id.as_str() == "impl" {
                        if let Some(first_arg) = call.args.first() {
                            if let Some(proto_name) = Self::last_name(first_arg) {
                                out.insert(proto_name);
                            }
                        }
                    }
                }
            }
        }
    }

    fn expr_to_string(expr: &Expr) -> String {
        match expr {
            Expr::Name(name) => name.id.to_string(),
            Expr::Attribute(attr) => {
                let base = Self::expr_to_string(&attr.value);
                format!("{}.{}", base, attr.attr)
            }
            Expr::Tuple(t) => t
                .elts
                .iter()
                .map(Self::expr_to_string)
                .collect::<Vec<_>>()
                .join(", "),
            _ => "<expr>".to_string(),
        }
    }
}

impl LintRule for AskResultTypeAnnotationRule {
    fn rule_id(&self) -> &str {
        "DOEFF030"
    }

    fn description(&self) -> &str {
        "ask(...) results must be assigned to typed variables"
    }

    fn check(&self, context: &RuleContext) -> Vec<Violation> {
        let mut violations = Vec::new();

        match context.stmt {
            Stmt::Assign(assign) => {
                if Self::get_yield_ask_call(&assign.value).is_some() {
                    let targets = assign
                        .targets
                        .iter()
                        .map(Self::expr_to_string)
                        .collect::<Vec<_>>()
                        .join(", ");

                    let message = format!(
                        "Result of 'ask' must be assigned to a variable with an inline type annotation.\n\n\
Problem: 'yield ask(...)' returns an injected value whose type cannot be inferred at the call site.\n\
Without a type annotation, downstream code loses type safety.\n\n\
Fix: Add a type annotation to the assignment:\n\
  {}: Type = yield ask(\"key\")",
                        targets
                    );

                    violations.push(Violation::new(
                        self.rule_id().to_string(),
                        message,
                        assign.range.start().to_usize(),
                        context.file_path.to_string(),
                        Severity::Warning,
                    ));
                }
            }
            Stmt::AnnAssign(ann_assign) => {
                if let Some(value) = &ann_assign.value {
                    if let Some(call) = Self::get_yield_ask_call(value) {
                        let Some(key_expr) = Self::get_ask_key_expr(call) else {
                            return violations;
                        };

                        // If the key is a string literal, this is a normal config ask.
                        if let Expr::Constant(constant) = key_expr {
                            if constant.value.is_str() {
                                return violations;
                            }
                        }

                        let Some(key_name) = Self::last_name(key_expr) else {
                            return violations;
                        };

                        // Heuristic: Only apply Protocol-specific checks for type-like keys.
                        if !Self::looks_like_type_name(&key_name) {
                            return violations;
                        }

                        let local_classes = Self::collect_local_classes(context.ast);
                        let protocols = Self::collect_protocols(context.ast);
                        let impl_protocols = Self::collect_impl_protocols(context.ast);

                        if local_classes.contains(&key_name) && !protocols.contains(&key_name) {
                            let message = format!(
                                "ask key '{}' looks like a type, but the class is not a Protocol.\n\n\
Problem: Callable/KleisliProgram injection should be keyed by a Protocol describing the signature.\n\n\
Fix: Define '{}' as a typing.Protocol and use it as both the annotation and ask key.",
                                key_name, key_name
                            );
                            violations.push(Violation::new(
                                self.rule_id().to_string(),
                                message,
                                ann_assign.range.start().to_usize(),
                                context.file_path.to_string(),
                                Severity::Warning,
                            ));
                        }

                        if !Self::expr_contains_name(&ann_assign.annotation, &key_name) {
                            let message = format!(
                                "Type annotation for ask({}) should use the same Protocol.\n\n\
Problem: When requesting a callable via ask, the variable type must match the Protocol key.\n\n\
Fix: Annotate as '{}':\n\
  value: {} = yield ask({})",
                                key_name, key_name, key_name, key_name
                            );
                            violations.push(Violation::new(
                                self.rule_id().to_string(),
                                message,
                                ann_assign.range.start().to_usize(),
                                context.file_path.to_string(),
                                Severity::Warning,
                            ));
                        }

                        if !impl_protocols.contains(&key_name) {
                            let message = format!(
                                "No @impl({}) provider found for Protocol-based ask.\n\n\
Problem: Protocol injection requires a provider function decorated with @impl({}).\n\n\
Fix: Add a provider:\n\
  @impl({})\n\
  @do\n\
  def provide_{}(...):\n\
      ...",
                                key_name, key_name, key_name, key_name.to_lowercase()
                            );
                            violations.push(Violation::new(
                                self.rule_id().to_string(),
                                message,
                                ann_assign.range.start().to_usize(),
                                context.file_path.to_string(),
                                Severity::Warning,
                            ));
                        }
                    }
                }
            }
            _ => {}
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
        let rule = AskResultTypeAnnotationRule::new();
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
    fn test_missing_annotation_for_ask() {
        let code = r#"
value = yield ask("project.a.use_b")
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("inline type annotation"));
    }

    #[test]
    fn test_annotation_present_ok() {
        let code = r#"
value: bool = yield ask("project.a.use_b")
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_dynamic_string_key_skipped_for_protocol_checks() {
        let code = r#"
key = "project.a.use_b"
value: bool = yield ask(key)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_uppercase_constant_key_skipped_for_protocol_checks() {
        let code = r#"
BUBBLE_WRAP_IMPL_ASK_KEY = "project.a.use_b"
value: bool = yield ask(BUBBLE_WRAP_IMPL_ASK_KEY)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_protocol_injection_ok() {
        let code = r#"
from typing import Protocol

class UploadFunc(Protocol):
    def __call__(self, bin): ...

uploader: UploadFunc = yield ask(UploadFunc)

@impl(UploadFunc)
def upload_gcs(bin): ...
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_protocol_key_without_impl() {
        let code = r#"
from typing import Protocol

class UploadFunc(Protocol):
    def __call__(self, bin): ...

uploader: UploadFunc = yield ask(UploadFunc)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("@impl"));
    }

    #[test]
    fn test_mismatched_annotation_for_protocol_key() {
        let code = r#"
from typing import Protocol, Callable

class UploadFunc(Protocol):
    def __call__(self, bin): ...

uploader: Callable = yield ask(UploadFunc)

@impl(UploadFunc)
def upload_gcs(bin): ...
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("Type annotation for ask"));
    }

    #[test]
    fn test_non_protocol_class_used_as_key() {
        let code = r#"
class UploadFunc:
    pass

uploader: UploadFunc = yield ask(UploadFunc)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 2);
        assert!(violations.iter().any(|v| v.message.contains("not a Protocol")));
        assert!(violations.iter().any(|v| v.message.contains("No @impl")));
    }
}

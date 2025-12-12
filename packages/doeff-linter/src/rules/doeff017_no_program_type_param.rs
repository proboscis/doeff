//! DOEFF017: No Program Type in Function Parameters
//!
//! @do functions typically accept the underlying type T, not Program[T].
//! doeff automatically resolves Program[T] arguments before executing the function body.
//!
//! However, if you explicitly annotate a parameter as Program[T], the @do wrapper
//! will NOT auto-unwrap the passed Program object. This is useful when writing
//! Program transforms (Program -> Program functions).

use crate::models::{RuleContext, Severity, Violation};
use crate::rules::base::LintRule;
use rustpython_ast::{Expr, Stmt};

pub struct NoProgramTypeParamRule;

impl NoProgramTypeParamRule {
    pub fn new() -> Self {
        Self
    }

    /// Check if a function has the @do decorator
    fn has_do_decorator(decorators: &[Expr]) -> bool {
        for decorator in decorators {
            match decorator {
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

    /// Check if the type annotation is Program or Program[T]
    fn is_program_type(expr: &Expr) -> bool {
        match expr {
            // Program (without type parameter)
            Expr::Name(name) => name.id.as_str() == "Program",
            // Program[T]
            Expr::Subscript(subscript) => {
                if let Expr::Name(name) = &*subscript.value {
                    name.id.as_str() == "Program"
                } else {
                    false
                }
            }
            // Handle Union types like Program[T] | None or Optional[Program[T]]
            Expr::BinOp(binop) => {
                Self::is_program_type(&binop.left) || Self::is_program_type(&binop.right)
            }
            _ => false,
        }
    }

    /// Extract type parameter from Program[T] annotation
    fn extract_type_param(expr: &Expr) -> Option<String> {
        match expr {
            Expr::Name(_) => None, // Program without type param
            Expr::Subscript(subscript) => {
                if let Expr::Name(name) = &*subscript.value {
                    if name.id.as_str() == "Program" {
                        return Some(Self::expr_to_string(&subscript.slice));
                    }
                }
                None
            }
            Expr::BinOp(binop) => {
                Self::extract_type_param(&binop.left)
                    .or_else(|| Self::extract_type_param(&binop.right))
            }
            _ => None,
        }
    }

    /// Convert an expression to a string representation
    fn expr_to_string(expr: &Expr) -> String {
        match expr {
            Expr::Name(name) => name.id.to_string(),
            Expr::Subscript(sub) => {
                let base = Self::expr_to_string(&sub.value);
                let slice = Self::expr_to_string(&sub.slice);
                format!("{}[{}]", base, slice)
            }
            Expr::Attribute(attr) => {
                let base = Self::expr_to_string(&attr.value);
                format!("{}.{}", base, attr.attr)
            }
            Expr::Tuple(tuple) => {
                let items: Vec<_> = tuple.elts.iter().map(Self::expr_to_string).collect();
                items.join(", ")
            }
            Expr::BinOp(binop) => {
                let left = Self::expr_to_string(&binop.left);
                let right = Self::expr_to_string(&binop.right);
                format!("{} | {}", left, right)
            }
            Expr::Constant(c) => {
                match &c.value {
                    rustpython_ast::Constant::None => "None".to_string(),
                    rustpython_ast::Constant::Str(s) => s.to_string(),
                    rustpython_ast::Constant::Int(i) => i.to_string(),
                    _ => "<constant>".to_string(),
                }
            }
            _ => "<type>".to_string(),
        }
    }

    /// Check function parameters for Program type annotations
    fn check_function_params(
        &self,
        func_name: &str,
        params: &rustpython_ast::Arguments,
        decorators: &[Expr],
        offset: usize,
        file_path: &str,
    ) -> Vec<Violation> {
        let mut violations = Vec::new();

        // Only check @do decorated functions
        if !Self::has_do_decorator(decorators) {
            return violations;
        }

        // Check all parameter types
        // Check regular args
        for arg in &params.args {
            if let Some(annotation) = &arg.def.annotation {
                if Self::is_program_type(annotation) {
                    violations.push(self.create_violation(
                        &arg.def.arg.to_string(),
                        func_name,
                        annotation,
                        offset,
                        file_path,
                    ));
                }
            }
        }

        // Check keyword-only args
        for arg in &params.kwonlyargs {
            if let Some(annotation) = &arg.def.annotation {
                if Self::is_program_type(annotation) {
                    violations.push(self.create_violation(
                        &arg.def.arg.to_string(),
                        func_name,
                        annotation,
                        offset,
                        file_path,
                    ));
                }
            }
        }

        // Check positional-only args
        for arg in &params.posonlyargs {
            if let Some(annotation) = &arg.def.annotation {
                if Self::is_program_type(annotation) {
                    violations.push(self.create_violation(
                        &arg.def.arg.to_string(),
                        func_name,
                        annotation,
                        offset,
                        file_path,
                    ));
                }
            }
        }

        // Check *args
        if let Some(vararg) = &params.vararg {
            if let Some(annotation) = &vararg.annotation {
                if Self::is_program_type(annotation) {
                    violations.push(self.create_violation(
                        &vararg.arg.to_string(),
                        func_name,
                        annotation,
                        offset,
                        file_path,
                    ));
                }
            }
        }

        // Check **kwargs
        if let Some(kwarg) = &params.kwarg {
            if let Some(annotation) = &kwarg.annotation {
                if Self::is_program_type(annotation) {
                    violations.push(self.create_violation(
                        &kwarg.arg.to_string(),
                        func_name,
                        annotation,
                        offset,
                        file_path,
                    ));
                }
            }
        }

        violations
    }

    fn create_violation(
        &self,
        param_name: &str,
        func_name: &str,
        annotation: &Expr,
        offset: usize,
        file_path: &str,
    ) -> Violation {
        let type_str = Self::expr_to_string(annotation);
        let inner_type = Self::extract_type_param(annotation)
            .unwrap_or_else(|| "T".to_string());

        let message = format!(
            "Function parameter '{}' in @do function '{}' has type '{}'.\n\n\
            Problem: @do functions typically accept the underlying type {}, not Program[{}].\n\
            By default, doeff auto-unwraps Program[{}] arguments before executing the function body.\n\n\
            Note: Using Program[{}] annotation prevents auto-unwrapping. This is intentional when:\n  \
            - Writing Program transforms (Program -> Program functions)\n  \
            - Explicitly composing Programs without resolving them\n  \
            If this is your intent, suppress with: # noqa: DOEFF017\n\n\
            Fix (if not intentional): Change the parameter type from '{}' to '{}':\n  \
            # Before\n  \
            @do\n  \
            def {}({}: {}) -> EffectGenerator[Result]: ...\n  \
            \n  \
            # After\n  \
            @do\n  \
            def {}({}: {}) -> EffectGenerator[Result]: ...",
            param_name,
            func_name,
            type_str,
            inner_type,
            inner_type,
            inner_type,
            inner_type,
            type_str,
            inner_type,
            func_name,
            param_name,
            type_str,
            func_name,
            param_name,
            inner_type,
        );

        Violation::new(
            self.rule_id().to_string(),
            message,
            offset,
            file_path.to_string(),
            Severity::Warning,
        )
    }
}

impl LintRule for NoProgramTypeParamRule {
    fn rule_id(&self) -> &str {
        "DOEFF017"
    }

    fn description(&self) -> &str {
        "@do functions typically accept type T, not Program[T] (Program[T] prevents auto-unwrap)"
    }

    fn check(&self, context: &RuleContext) -> Vec<Violation> {
        match context.stmt {
            Stmt::FunctionDef(func) => self.check_function_params(
                func.name.as_str(),
                &func.args,
                &func.decorator_list,
                func.range.start().to_usize(),
                context.file_path,
            ),
            Stmt::AsyncFunctionDef(func) => self.check_function_params(
                func.name.as_str(),
                &func.args,
                &func.decorator_list,
                func.range.start().to_usize(),
                context.file_path,
            ),
            _ => Vec::new(),
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
        let rule = NoProgramTypeParamRule::new();
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
    fn test_program_type_param_detected() {
        let code = r#"
@do
def process(data: Program[DataFrame]) -> EffectGenerator[Result]:
    pass
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("data"));
        assert!(violations[0].message.contains("Program[DataFrame]"));
    }

    #[test]
    fn test_program_type_without_param_detected() {
        let code = r#"
@do
def process(data: Program) -> EffectGenerator[Result]:
    pass
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("data"));
        assert!(violations[0].message.contains("Program"));
    }

    #[test]
    fn test_underlying_type_allowed() {
        let code = r#"
@do
def process(data: DataFrame) -> EffectGenerator[Result]:
    pass
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_non_do_function_ignored() {
        let code = r#"
def process(data: Program[DataFrame]) -> Result:
    pass
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_multiple_params_with_program() {
        let code = r#"
@do
def process(data: Program[DataFrame], config: Program[Config]) -> EffectGenerator[Result]:
    pass
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 2);
    }

    #[test]
    fn test_mixed_params() {
        let code = r#"
@do
def process(data: Program[DataFrame], threshold: float) -> EffectGenerator[Result]:
    pass
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("data"));
    }

    #[test]
    fn test_do_with_call_syntax() {
        let code = r#"
@do()
def process(data: Program[DataFrame]) -> EffectGenerator[Result]:
    pass
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_async_function() {
        let code = r#"
@do
async def process(data: Program[DataFrame]) -> EffectGenerator[Result]:
    pass
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_keyword_only_args() {
        let code = r#"
@do
def process(*, data: Program[DataFrame]) -> EffectGenerator[Result]:
    pass
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_union_with_program() {
        let code = r#"
@do
def process(data: Program[DataFrame] | None) -> EffectGenerator[Result]:
    pass
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_error_message_contains_fix() {
        let code = r#"
@do
def process(data: Program[DataFrame]) -> EffectGenerator[Result]:
    pass
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("# Before"));
        assert!(violations[0].message.contains("# After"));
        assert!(violations[0].message.contains("DataFrame"));
    }

    #[test]
    fn test_message_explains_intentional_use() {
        let code = r#"
@do
def transform_program(p: Program[DataFrame]) -> EffectGenerator[Program[Result]]:
    pass
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        // Message should explain that Program annotation prevents auto-unwrapping
        assert!(violations[0].message.contains("prevents auto-unwrap"));
        // Message should mention Program transforms as valid use case
        assert!(violations[0].message.contains("Program transform"));
        // Message should suggest noqa suppression for intentional use
        assert!(violations[0].message.contains("noqa: DOEFF017"));
    }

    #[test]
    fn test_severity_is_warning() {
        let code = r#"
@do
def process(data: Program[DataFrame]) -> EffectGenerator[Result]:
    pass
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert_eq!(violations[0].severity, Severity::Warning);
    }

    #[test]
    fn test_no_annotation_ignored() {
        let code = r#"
@do
def process(data) -> EffectGenerator[Result]:
    pass
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }
}


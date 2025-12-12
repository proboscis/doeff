//! DOEFF013: Prefer Maybe Monad
//!
//! Detects Optional[X] or X | None type annotations and suggests using doeff's Maybe monad instead.

use crate::models::{RuleContext, Severity, Violation};
use crate::rules::base::LintRule;
use rustpython_ast::{Expr, Stmt};

pub struct PreferMaybeMonadRule;

impl PreferMaybeMonadRule {
    pub fn new() -> Self {
        Self
    }

    /// Check if an expression represents Optional[X], X | None, or None | X
    fn is_optional_type(expr: &Expr) -> bool {
        match expr {
            // Check for Optional[X] - unqualified
            Expr::Subscript(subscript) => {
                if let Expr::Name(name) = &*subscript.value {
                    if name.id.as_str() == "Optional" {
                        return true;
                    }
                    // Check for Union[X, None] or Union[None, X]
                    if name.id.as_str() == "Union" {
                        return Self::is_union_with_none(&subscript.slice);
                    }
                }
                // Check for typing.Optional[X] - qualified
                if let Expr::Attribute(attr) = &*subscript.value {
                    if let Expr::Name(module) = &*attr.value {
                        if module.id.as_str() == "typing" {
                            if attr.attr.as_str() == "Optional" {
                                return true;
                            }
                            // Check for typing.Union[X, None]
                            if attr.attr.as_str() == "Union" {
                                return Self::is_union_with_none(&subscript.slice);
                            }
                        }
                    }
                }
                false
            }
            // Check for X | None or None | X (Python 3.10+ union syntax)
            Expr::BinOp(binop) => {
                if matches!(binop.op, rustpython_ast::Operator::BitOr) {
                    // Check if either side is None
                    let left_is_none = Self::is_none_constant(&binop.left);
                    let right_is_none = Self::is_none_constant(&binop.right);
                    return left_is_none || right_is_none;
                }
                false
            }
            _ => false,
        }
    }

    /// Check if an expression is None constant
    fn is_none_constant(expr: &Expr) -> bool {
        match expr {
            // Check for Constant::None variant
            Expr::Constant(constant) => {
                matches!(constant.value, rustpython_ast::Constant::None)
            }
            // Check for Name("None") which is how None appears in type annotations
            Expr::Name(name) => name.id.as_str() == "None",
            _ => false,
        }
    }

    /// Check if Union contains None
    fn is_union_with_none(slice: &Expr) -> bool {
        match slice {
            Expr::Tuple(tuple) => tuple.elts.iter().any(Self::is_none_constant),
            // Single element union (unlikely but possible)
            _ => Self::is_none_constant(slice),
        }
    }

    /// Get the type string from Optional or Union for display
    fn get_inner_type_display(expr: &Expr) -> String {
        match expr {
            Expr::Subscript(subscript) => {
                if let Expr::Name(name) = &*subscript.value {
                    if name.id.as_str() == "Optional" || name.id.as_str() == "Union" {
                        return Self::extract_non_none_types(&subscript.slice);
                    }
                }
                if let Expr::Attribute(attr) = &*subscript.value {
                    if let Expr::Name(module) = &*attr.value {
                        if module.id.as_str() == "typing"
                            && (attr.attr.as_str() == "Optional" || attr.attr.as_str() == "Union")
                        {
                            return Self::extract_non_none_types(&subscript.slice);
                        }
                    }
                }
                "T".to_string()
            }
            Expr::BinOp(binop) => {
                if Self::is_none_constant(&binop.left) {
                    Self::expr_to_type_string(&binop.right)
                } else {
                    Self::expr_to_type_string(&binop.left)
                }
            }
            _ => "T".to_string(),
        }
    }

    fn extract_non_none_types(slice: &Expr) -> String {
        match slice {
            Expr::Tuple(tuple) => {
                let non_none: Vec<_> = tuple
                    .elts
                    .iter()
                    .filter(|e| !Self::is_none_constant(e))
                    .map(Self::expr_to_type_string)
                    .collect();
                if non_none.is_empty() {
                    "T".to_string()
                } else if non_none.len() == 1 {
                    non_none[0].clone()
                } else {
                    non_none.join(" | ")
                }
            }
            _ => {
                if Self::is_none_constant(slice) {
                    "T".to_string()
                } else {
                    Self::expr_to_type_string(slice)
                }
            }
        }
    }

    fn expr_to_type_string(expr: &Expr) -> String {
        match expr {
            Expr::Name(name) => name.id.to_string(),
            Expr::Subscript(sub) => {
                let base = Self::expr_to_type_string(&sub.value);
                let slice = Self::expr_to_type_string(&sub.slice);
                format!("{}[{}]", base, slice)
            }
            Expr::Attribute(attr) => {
                let value = Self::expr_to_type_string(&attr.value);
                format!("{}.{}", value, attr.attr)
            }
            Expr::Tuple(tuple) => {
                let items: Vec<_> = tuple.elts.iter().map(Self::expr_to_type_string).collect();
                items.join(", ")
            }
            _ => "T".to_string(),
        }
    }

    fn create_violation(
        rule_id: &str,
        context_name: &str,
        context_type: &str,
        inner_type: &str,
        offset: usize,
        file_path: &str,
    ) -> Violation {
        Violation::new(
            rule_id.to_string(),
            format!(
                "{} '{}' uses Optional/None type annotation. Consider using doeff's Maybe monad instead for explicit null handling. \
                 Example: Instead of '{}' or '{} | None', use 'Maybe[{}]'. \
                 Import with 'from doeff import Maybe, Some, NOTHING' and create values with 'Some(value)' or 'NOTHING'. \
                 Use 'Maybe.from_optional(value)' to convert existing Optional values.",
                context_type,
                context_name,
                format!("Optional[{}]", inner_type),
                inner_type,
                inner_type
            ),
            offset,
            file_path.to_string(),
            Severity::Warning,
        )
    }

    fn check_function_params_and_return(
        name: &str,
        args: &rustpython_ast::Arguments,
        returns: &Option<Box<Expr>>,
        range_start: usize,
        file_path: &str,
    ) -> Vec<Violation> {
        let mut violations = Vec::new();

        // Check return type
        if let Some(return_type) = returns {
            if Self::is_optional_type(return_type) {
                let inner_type = Self::get_inner_type_display(return_type);
                violations.push(Self::create_violation(
                    "DOEFF013",
                    name,
                    "Function return type",
                    &inner_type,
                    range_start,
                    file_path,
                ));
            }
        }

        // Check parameter annotations
        for arg in args.args.iter().chain(args.posonlyargs.iter()) {
            if let Some(annotation) = &arg.def.annotation {
                if Self::is_optional_type(annotation) {
                    let inner_type = Self::get_inner_type_display(annotation);
                    violations.push(Self::create_violation(
                        "DOEFF013",
                        &format!("{} (param '{}')", name, arg.def.arg),
                        "Function parameter",
                        &inner_type,
                        range_start,
                        file_path,
                    ));
                }
            }
        }

        // Check keyword-only arguments
        for arg in &args.kwonlyargs {
            if let Some(annotation) = &arg.def.annotation {
                if Self::is_optional_type(annotation) {
                    let inner_type = Self::get_inner_type_display(annotation);
                    violations.push(Self::create_violation(
                        "DOEFF013",
                        &format!("{} (param '{}')", name, arg.def.arg),
                        "Function parameter",
                        &inner_type,
                        range_start,
                        file_path,
                    ));
                }
            }
        }

        violations
    }
}

impl LintRule for PreferMaybeMonadRule {
    fn rule_id(&self) -> &str {
        "DOEFF013"
    }

    fn description(&self) -> &str {
        "Prefer doeff's Maybe monad over Optional/None type annotations"
    }

    fn check(&self, context: &RuleContext) -> Vec<Violation> {
        let mut violations = Vec::new();

        match context.stmt {
            Stmt::FunctionDef(func) => {
                violations.extend(Self::check_function_params_and_return(
                    func.name.as_str(),
                    &func.args,
                    &func.returns,
                    func.range.start().to_usize(),
                    context.file_path,
                ));
            }
            Stmt::AsyncFunctionDef(func) => {
                violations.extend(Self::check_function_params_and_return(
                    func.name.as_str(),
                    &func.args,
                    &func.returns,
                    func.range.start().to_usize(),
                    context.file_path,
                ));
            }
            // Check annotated assignments (variable type hints)
            Stmt::AnnAssign(ann_assign) => {
                if Self::is_optional_type(&ann_assign.annotation) {
                    let var_name = match &*ann_assign.target {
                        Expr::Name(name) => name.id.to_string(),
                        _ => "variable".to_string(),
                    };
                    let inner_type = Self::get_inner_type_display(&ann_assign.annotation);
                    violations.push(Self::create_violation(
                        "DOEFF013",
                        &var_name,
                        "Variable annotation",
                        &inner_type,
                        ann_assign.range.start().to_usize(),
                        context.file_path,
                    ));
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
        let rule = PreferMaybeMonadRule::new();
        let mut violations = Vec::new();

        fn check_stmts(
            stmts: &[Stmt],
            rule: &PreferMaybeMonadRule,
            violations: &mut Vec<Violation>,
            code: &str,
            ast: &Mod,
        ) {
            for stmt in stmts {
                let context = RuleContext {
                    stmt,
                    file_path: "test.py",
                    source: code,
                    ast,
                };
                violations.extend(rule.check(&context));

                // Recursively check nested statements
                match stmt {
                    Stmt::ClassDef(class) => {
                        check_stmts(&class.body, rule, violations, code, ast);
                    }
                    Stmt::FunctionDef(func) => {
                        check_stmts(&func.body, rule, violations, code, ast);
                    }
                    Stmt::AsyncFunctionDef(func) => {
                        check_stmts(&func.body, rule, violations, code, ast);
                    }
                    _ => {}
                }
            }
        }

        if let Mod::Module(module) = &ast {
            check_stmts(&module.body, &rule, &mut violations, code, &ast);
        }

        violations
    }

    #[test]
    fn test_optional_return_type() {
        let code = r#"
from typing import Optional

def get_user(id: int) -> Optional[str]:
    return None
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("Optional"));
        assert!(violations[0].message.contains("Maybe"));
    }

    #[test]
    fn test_union_none_syntax() {
        let code = r#"
def get_value() -> str | None:
    return None
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("Maybe"));
    }

    #[test]
    fn test_none_union_syntax_reversed() {
        let code = r#"
def get_value() -> None | int:
    return None
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_optional_parameter() {
        let code = r#"
from typing import Optional

def process(value: Optional[int]) -> int:
    return value or 0
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("param"));
    }

    #[test]
    fn test_union_parameter() {
        let code = r#"
def process(value: int | None) -> int:
    return value or 0
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_typing_optional_qualified() {
        let code = r#"
import typing

def get_data() -> typing.Optional[dict]:
    return None
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_variable_annotation() {
        let code = r#"
from typing import Optional

result: Optional[str] = None
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("Variable"));
    }

    #[test]
    fn test_variable_union_annotation() {
        let code = r#"
value: int | None = None
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_union_with_none() {
        let code = r#"
from typing import Union

def get_value() -> Union[str, None]:
    return None
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_non_optional_allowed() {
        let code = r#"
def get_user() -> str:
    return "Alice"

def add(a: int, b: int) -> int:
    return a + b
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_maybe_allowed() {
        let code = r#"
from doeff import Maybe

def get_user() -> Maybe[str]:
    return Some("Alice")
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_async_function() {
        let code = r#"
from typing import Optional

async def fetch_data() -> Optional[bytes]:
    return None
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("fetch_data"));
    }

    #[test]
    fn test_multiple_optional_params() {
        let code = r#"
from typing import Optional

def multi(a: Optional[int], b: str | None, c: int) -> Optional[str]:
    return None
"#;
        let violations = check_code(code);
        // a: Optional[int], b: str | None, return Optional[str] = 3 violations
        assert_eq!(violations.len(), 3);
    }

    #[test]
    fn test_class_method() {
        let code = r#"
from typing import Optional

class MyClass:
    def get_value(self, key: str) -> Optional[int]:
        return None
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }
}


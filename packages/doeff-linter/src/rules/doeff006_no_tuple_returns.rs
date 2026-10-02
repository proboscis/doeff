//! DOEFF006: No Tuple Returns
//!
//! Functions should not return tuples. Use dataclasses for structured return values.

use crate::models::{RuleContext, Severity, Violation};
use crate::rules::base::LintRule;
use rustpython_ast::{Constant, Expr, Stmt, StmtAsyncFunctionDef, StmtFunctionDef};

pub struct NoTupleReturnsRule;

impl NoTupleReturnsRule {
    pub fn new() -> Self {
        Self
    }

    /// `tuple[X, ...]` の添字か — 同じ型の可変長の列(型のある列)で、複数の値を位置で返す組ではない
    /// (agora-redesign #2031: packages/doeff-cluster の検の補助が `-> tuple[str, ...]` で名の列を返して誤って当たった。
    /// DOEFF171 も同じ形を型のある列として受ける)。
    fn is_homogeneous_sequence(slice: &Expr) -> bool {
        match slice {
            Expr::Tuple(tuple) => {
                tuple.elts.len() == 2
                    && matches!(&tuple.elts[1], Expr::Constant(c) if matches!(c.value, Constant::Ellipsis))
            }
            _ => false,
        }
    }

    fn is_tuple_type(expr: &Expr) -> bool {
        match expr {
            Expr::Name(name) => {
                let name_str = name.id.as_str();
                name_str == "tuple" || name_str == "Tuple"
            }
            Expr::Subscript(subscript) => {
                if let Expr::Name(name) = &*subscript.value {
                    let name_str = name.id.as_str();
                    if name_str == "tuple" || name_str == "Tuple" {
                        return !Self::is_homogeneous_sequence(&subscript.slice);
                    }
                    if name_str == "Optional" || name_str == "Union" {
                        if let Expr::Tuple(tuple) = &*subscript.slice {
                            return tuple.elts.iter().any(Self::is_tuple_type);
                        } else {
                            return Self::is_tuple_type(&subscript.slice);
                        }
                    }
                } else if let Expr::Attribute(attr) = &*subscript.value {
                    if let Expr::Name(module) = &*attr.value {
                        if module.id.as_str() == "typing" {
                            if attr.attr.as_str() == "Tuple" {
                                return !Self::is_homogeneous_sequence(&subscript.slice);
                            }
                            if attr.attr.as_str() == "Optional" || attr.attr.as_str() == "Union" {
                                if let Expr::Tuple(tuple) = &*subscript.slice {
                                    return tuple.elts.iter().any(Self::is_tuple_type);
                                } else {
                                    return Self::is_tuple_type(&subscript.slice);
                                }
                            }
                        }
                    }
                }
                false
            }
            Expr::Attribute(attr) => {
                if let Expr::Name(module) = &*attr.value {
                    module.id.as_str() == "typing" && attr.attr.as_str() == "Tuple"
                } else {
                    false
                }
            }
            Expr::BinOp(binop) => {
                Self::is_tuple_type(&binop.left) || Self::is_tuple_type(&binop.right)
            }
            _ => false,
        }
    }

    fn check_function(func: &StmtFunctionDef, file_path: &str) -> Option<Violation> {
        if let Some(returns) = &func.returns {
            if Self::is_tuple_type(returns) {
                return Some(Violation::new(
                    "DOEFF006".to_string(),
                    format!(
                        "Function '{}' returns a tuple. Use a dataclass instead for structured return values. \
                         Example: Instead of 'def get_user() -> tuple[str, int]', \
                         use '@dataclass class User: name: str; age: int' and 'def get_user() -> User'.",
                        func.name
                    ),
                    func.range.start().to_usize(),
                    file_path.to_string(),
                    Severity::Error,
                ));
            }
        }
        None
    }

    fn check_async_function(func: &StmtAsyncFunctionDef, file_path: &str) -> Option<Violation> {
        if let Some(returns) = &func.returns {
            if Self::is_tuple_type(returns) {
                return Some(Violation::new(
                    "DOEFF006".to_string(),
                    format!(
                        "Async function '{}' returns a tuple. Use a dataclass instead.",
                        func.name
                    ),
                    func.range.start().to_usize(),
                    file_path.to_string(),
                    Severity::Error,
                ));
            }
        }
        None
    }
}

impl LintRule for NoTupleReturnsRule {
    fn rule_id(&self) -> &str {
        "DOEFF006"
    }

    fn description(&self) -> &str {
        "Functions should not return tuples. Use dataclasses instead."
    }

    fn check(&self, context: &RuleContext) -> Vec<Violation> {
        let mut violations = Vec::new();

        match context.stmt {
            Stmt::FunctionDef(func) => {
                if let Some(v) = Self::check_function(func, context.file_path) {
                    violations.push(v);
                }
            }
            Stmt::AsyncFunctionDef(func) => {
                if let Some(v) = Self::check_async_function(func, context.file_path) {
                    violations.push(v);
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
        let rule = NoTupleReturnsRule::new();
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
    fn test_tuple_return_type() {
        let code = r#"
from typing import Tuple

def get_user() -> Tuple[str, int]:
    return ("Alice", 25)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("get_user"));
    }

    #[test]
    fn test_lowercase_tuple() {
        let code = r#"
def get_coords() -> tuple[float, float]:
    return (1.0, 2.0)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_a_homogeneous_sequence_is_not_a_tuple_return() {
        // agora-redesign #2031: `tuple[X, ...]` は同じ型の可変長の列 — 位置で複数の値を返す組ではない。
        let code = r#"
from typing import Tuple

def names() -> tuple[str, ...]:
    return ("a", "b")

def rows() -> Tuple[int, ...]:
    return (1, 2, 3)

def maybe_names() -> tuple[str, ...] | None:
    return None
"#;
        assert_eq!(check_code(code).len(), 0);
    }

    #[test]
    fn test_a_fixed_pair_still_fires_beside_a_sequence() {
        // 失敗ケース: 位置で値を返す組(2 つ・3 つ・入れ子)は今までどおり当たる — 省略記号の無い組だけを外していない。
        let code = r#"
def pair() -> tuple[str, int]:
    return ("a", 1)

def triple() -> tuple[str, str, str]:
    return ("a", "b", "c")

def nested() -> tuple[tuple[str, int], ...] | tuple[str, int]:
    return ()
"#;
        assert_eq!(check_code(code).len(), 3);
    }

    #[test]
    fn test_dataclass_return_allowed() {
        let code = r#"
from dataclasses import dataclass

@dataclass
class User:
    name: str
    age: int

def get_user() -> User:
    return User("Alice", 25)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_optional_tuple() {
        let code = r#"
from typing import Optional, Tuple

def maybe_get() -> Optional[Tuple[str, int]]:
    return None
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }
}




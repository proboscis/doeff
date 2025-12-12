//! DOEFF022: Prefer @do Decorated Functions
//!
//! Functions should use the @do decorator to enable structured effects,
//! logging with `yield slog`, and composition with other Programs.

use crate::models::{RuleContext, Severity, Violation};
use crate::rules::base::LintRule;
use rustpython_ast::{Expr, Stmt};

pub struct PreferDoFunctionRule {
    skip_private: bool,
}

impl PreferDoFunctionRule {
    pub fn new() -> Self {
        Self { skip_private: false }
    }

    #[allow(dead_code)]
    pub fn with_skip_private(skip_private: bool) -> Self {
        Self { skip_private }
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

    /// Check if the function has decorators that indicate it shouldn't use @do
    fn has_excluding_decorator(decorators: &[Expr]) -> bool {
        let excluding_decorators = [
            "property",
            "staticmethod",
            "classmethod",
            "abstractmethod",
            "abstractproperty",
            "cached_property",
            "dataclass",
            "fixture",
            "pytest.fixture",
            "override",
            "final",
        ];

        for decorator in decorators {
            match decorator {
                Expr::Name(name) => {
                    if excluding_decorators.contains(&name.id.as_str()) {
                        return true;
                    }
                }
                Expr::Attribute(attr) => {
                    // Handle pytest.fixture, etc.
                    let attr_name = attr.attr.as_str();
                    if excluding_decorators.contains(&attr_name) {
                        return true;
                    }
                }
                Expr::Call(call) => {
                    match &*call.func {
                        Expr::Name(name) => {
                            if excluding_decorators.contains(&name.id.as_str()) {
                                return true;
                            }
                        }
                        Expr::Attribute(attr) => {
                            let attr_name = attr.attr.as_str();
                            if excluding_decorators.contains(&attr_name) {
                                return true;
                            }
                        }
                        _ => {}
                    }
                }
                _ => {}
            }
        }
        false
    }

    /// Check if this function should be skipped
    fn should_skip(&self, func_name: &str, decorators: &[Expr], is_method: bool) -> bool {
        // Skip dunder methods (special methods)
        if func_name.starts_with("__") && func_name.ends_with("__") {
            return true;
        }

        // Skip test functions
        if func_name.starts_with("test_") {
            return true;
        }

        // Skip pytest fixtures (often named without test_ prefix)
        if func_name.ends_with("_fixture") || func_name == "fixture" {
            return true;
        }

        // Skip private functions if configured
        if self.skip_private && func_name.starts_with('_') {
            return true;
        }

        // Skip methods with excluding decorators
        if Self::has_excluding_decorator(decorators) {
            return true;
        }

        // Skip main function (common entry point)
        if func_name == "main" {
            return true;
        }

        // Skip setup/teardown functions (pytest/unittest)
        if matches!(
            func_name,
            "setUp" | "tearDown" | "setUpClass" | "tearDownClass" | "setUpModule" | "tearDownModule"
        ) {
            return true;
        }

        // Skip if this appears to be a simple getter without side effects
        // Methods starting with "get_" that don't have self mutation are common
        // But we'll still flag them since @do is preferred

        // For methods (self/cls as first arg), allow without @do in certain cases
        if is_method {
            // Allow simple dunder-like patterns
            if func_name.starts_with("_") && !func_name.starts_with("__") {
                // Private methods are okay to not have @do (they're internal implementation)
                return true;
            }
        }

        false
    }

    fn is_method(args: &rustpython_ast::Arguments) -> bool {
        if let Some(first_arg) = args.args.first() {
            let arg_name = first_arg.def.arg.as_str();
            return arg_name == "self" || arg_name == "cls";
        }
        if let Some(first_arg) = args.posonlyargs.first() {
            let arg_name = first_arg.def.arg.as_str();
            return arg_name == "self" || arg_name == "cls";
        }
        false
    }
}

impl LintRule for PreferDoFunctionRule {
    fn rule_id(&self) -> &str {
        "DOEFF022"
    }

    fn description(&self) -> &str {
        "Functions should use @do decorator for structured effects"
    }

    fn check(&self, context: &RuleContext) -> Vec<Violation> {
        let mut violations = Vec::new();

        match context.stmt {
            Stmt::FunctionDef(func) => {
                let is_method = Self::is_method(&func.args);

                // Skip if should be excluded
                if self.should_skip(func.name.as_str(), &func.decorator_list, is_method) {
                    return violations;
                }

                // Check if it already has @do decorator
                if Self::has_do_decorator(&func.decorator_list) {
                    return violations;
                }

                let func_type = if is_method { "Method" } else { "Function" };
                let message = format!(
                    "{} '{}' is not decorated with @do.\n\n\
                    Recommendation: Consider using the @do decorator to enable doeff's structured effects:\n  \
                    - Effect tracking for IO, async, and side effects\n  \
                    - Structured logging with `yield slog(\"message\", key=value)`\n  \
                    - Composition with other Program functions\n\n\
                    Example:\n  \
                    # Before\n  \
                    def {}(...) -> ReturnType:\n      \
                    ...\n  \n  \
                    # After\n  \
                    @do\n  \
                    def {}(...) -> EffectGenerator[ReturnType]:\n      \
                    yield slog(\"Processing\", ...)  # optional structured logging\n      \
                    ...\n\n\
                    If this function intentionally doesn't use doeff effects, suppress with: # noqa: DOEFF022",
                    func_type,
                    func.name,
                    func.name,
                    func.name
                );

                violations.push(Violation::new(
                    self.rule_id().to_string(),
                    message,
                    func.range.start().to_usize(),
                    context.file_path.to_string(),
                    Severity::Info,
                ));
            }
            Stmt::AsyncFunctionDef(func) => {
                let is_method = if let Some(first_arg) = func.args.args.first() {
                    let arg_name = first_arg.def.arg.as_str();
                    arg_name == "self" || arg_name == "cls"
                } else {
                    false
                };

                // Skip if should be excluded
                if self.should_skip(func.name.as_str(), &func.decorator_list, is_method) {
                    return violations;
                }

                // Check if it already has @do decorator
                if Self::has_do_decorator(&func.decorator_list) {
                    return violations;
                }

                let func_type = if is_method {
                    "Async method"
                } else {
                    "Async function"
                };
                let message = format!(
                    "{} '{}' is not decorated with @do.\n\n\
                    Recommendation: Consider using the @do decorator to enable doeff's structured effects:\n  \
                    - Effect tracking for IO, async, and side effects\n  \
                    - Structured logging with `yield slog(\"message\", key=value)`\n  \
                    - Composition with other Program functions\n\n\
                    Example:\n  \
                    # Before\n  \
                    async def {}(...) -> ReturnType:\n      \
                    ...\n  \n  \
                    # After\n  \
                    @do\n  \
                    async def {}(...) -> EffectGenerator[ReturnType]:\n      \
                    yield slog(\"Processing\", ...)  # optional structured logging\n      \
                    ...\n\n\
                    If this function intentionally doesn't use doeff effects, suppress with: # noqa: DOEFF022",
                    func_type,
                    func.name,
                    func.name,
                    func.name
                );

                violations.push(Violation::new(
                    self.rule_id().to_string(),
                    message,
                    func.range.start().to_usize(),
                    context.file_path.to_string(),
                    Severity::Info,
                ));
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
        let rule = PreferDoFunctionRule::new();
        let mut violations = Vec::new();

        fn check_stmts(
            stmts: &[Stmt],
            rule: &PreferDoFunctionRule,
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

                // Recurse into class bodies
                match stmt {
                    Stmt::ClassDef(class) => {
                        check_stmts(&class.body, rule, violations, code, ast);
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
    fn test_function_without_do_flagged() {
        let code = r#"
def process_data(data: Data) -> Result:
    return Result()
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("process_data"));
        assert!(violations[0].message.contains("@do"));
        assert!(violations[0].message.contains("yield slog"));
    }

    #[test]
    fn test_function_with_do_allowed() {
        let code = r#"
@do
def process_data(data: Data) -> EffectGenerator[Result]:
    yield slog("Processing")
    return Result()
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_function_with_do_call_allowed() {
        let code = r#"
@do()
def process_data(data: Data) -> EffectGenerator[Result]:
    return Result()
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_dunder_method_skipped() {
        let code = r#"
class MyClass:
    def __init__(self, value: int):
        self.value = value
    
    def __str__(self) -> str:
        return str(self.value)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_test_function_skipped() {
        let code = r#"
def test_my_feature():
    assert True
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_property_skipped() {
        let code = r#"
class MyClass:
    @property
    def value(self) -> int:
        return self._value
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_staticmethod_skipped() {
        let code = r#"
class MyClass:
    @staticmethod
    def create() -> MyClass:
        return MyClass()
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_classmethod_skipped() {
        let code = r#"
class MyClass:
    @classmethod
    def from_dict(cls, data: dict) -> MyClass:
        return cls()
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_private_method_skipped() {
        let code = r#"
class MyClass:
    def _internal_helper(self) -> int:
        return 42
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_main_function_skipped() {
        let code = r#"
def main():
    print("Hello")
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_async_function_without_do_flagged() {
        let code = r#"
async def fetch_data(url: str) -> Data:
    return Data()
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("Async function"));
        assert!(violations[0].message.contains("fetch_data"));
    }

    #[test]
    fn test_async_function_with_do_allowed() {
        let code = r#"
@do
async def fetch_data(url: str) -> EffectGenerator[Data]:
    return Data()
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_public_method_flagged() {
        let code = r#"
class DataProcessor:
    def process(self, data: Data) -> Result:
        return Result()
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("Method"));
        assert!(violations[0].message.contains("process"));
    }

    #[test]
    fn test_severity_is_info() {
        let code = r#"
def process_data(data: Data) -> Result:
    return Result()
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert_eq!(violations[0].severity, Severity::Info);
    }

    #[test]
    fn test_noqa_mentioned_in_message() {
        let code = r#"
def process_data(data: Data) -> Result:
    return Result()
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("noqa: DOEFF022"));
    }

    #[test]
    fn test_fixture_decorator_skipped() {
        let code = r#"
@pytest.fixture
def sample_data():
    return Data()
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_abstractmethod_skipped() {
        let code = r#"
class AbstractProcessor:
    @abstractmethod
    def process(self, data: Data) -> Result:
        pass
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_setup_teardown_skipped() {
        let code = r#"
class TestMyFeature:
    def setUp(self):
        pass
    
    def tearDown(self):
        pass
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }
}


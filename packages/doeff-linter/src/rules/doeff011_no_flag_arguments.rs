//! DOEFF011: No Flag/Mode Arguments
//!
//! Functions and dataclasses should not use flag/mode arguments.
//! Instead of passing flags/modes and switching with if statements inside,
//! accept a callback or protocol object that implements the varying behavior.

use crate::models::{RuleContext, Severity, Violation};
use crate::rules::base::LintRule;
use crate::utils::has_dataclass_decorator;
use rustpython_ast::{Arguments, Constant, Expr, Stmt, StmtAsyncFunctionDef, StmtClassDef, StmtFunctionDef};

pub struct NoFlagArgumentsRule {
    /// Skip parameters with these exact names
    skip_names: Vec<String>,
}

impl NoFlagArgumentsRule {
    pub fn new() -> Self {
        Self {
            skip_names: vec![
                // Common legitimate boolean parameters
                "return_exceptions".to_string(),
                "keep_alive".to_string(),
            ],
        }
    }

    /// Check if a parameter name suggests a flag/mode
    fn is_flag_like_name(name: &str) -> bool {
        let name_lower = name.to_lowercase();
        
        // Prefix patterns that suggest flags
        let flag_prefixes = [
            "is_", "has_", "use_", "enable_", "disable_", "with_", "without_",
            "should_", "can_", "allow_", "no_", "skip_", "include_", "exclude_",
        ];
        
        // Suffix patterns that suggest flags
        let flag_suffixes = [
            "_enabled", "_disabled", "_flag", "_mode", "_option", "_only",
            "_first", "_last", "_all", "_none", "_strict", "_lenient",
        ];
        
        // Exact names that suggest flags/modes
        let flag_names = [
            "verbose", "debug", "quiet", "silent", "strict", "lenient",
            "force", "recursive", "dry_run", "dryrun", "mode", "flag",
            "option", "style", "format", "variant", "kind", "type_",
            "async_", "sync", "blocking", "nonblocking", "parallel",
            "sequential", "ascending", "descending", "reverse", "reversed",
            "inplace", "in_place", "copy", "deep", "shallow",
        ];
        
        for prefix in &flag_prefixes {
            if name_lower.starts_with(prefix) {
                return true;
            }
        }
        
        for suffix in &flag_suffixes {
            if name_lower.ends_with(suffix) {
                return true;
            }
        }
        
        for flag_name in &flag_names {
            if name_lower == *flag_name {
                return true;
            }
        }
        
        false
    }

    /// Check if a type annotation is a boolean
    fn is_bool_annotation(expr: &Expr) -> bool {
        match expr {
            Expr::Name(name) => name.id.as_str() == "bool",
            Expr::BinOp(binop) => {
                // Handle Union types like bool | None
                Self::is_bool_annotation(&binop.left) || Self::is_bool_annotation(&binop.right)
            }
            Expr::Subscript(subscript) => {
                // Handle Optional[bool], Union[bool, None], etc.
                if let Expr::Name(name) = &*subscript.value {
                    if name.id.as_str() == "Optional" {
                        return Self::is_bool_annotation(&subscript.slice);
                    }
                    if name.id.as_str() == "Union" {
                        if let Expr::Tuple(tuple) = &*subscript.slice {
                            return tuple.elts.iter().any(Self::is_bool_annotation);
                        }
                    }
                }
                false
            }
            _ => false,
        }
    }

    /// Check if a type annotation is a Literal with few options (mode-like)
    fn is_mode_literal(expr: &Expr) -> Option<Vec<String>> {
        match expr {
            Expr::Subscript(subscript) => {
                if let Expr::Name(name) = &*subscript.value {
                    if name.id.as_str() == "Literal" {
                        let mut options = Vec::new();
                        match &*subscript.slice {
                            Expr::Tuple(tuple) => {
                                for elt in &tuple.elts {
                                    if let Expr::Constant(c) = elt {
                                        if let Constant::Str(s) = &c.value {
                                            options.push(s.to_string());
                                        }
                                    }
                                }
                            }
                            Expr::Constant(c) => {
                                if let Constant::Str(s) = &c.value {
                                    options.push(s.to_string());
                                }
                            }
                            _ => {}
                        }
                        // Only flag if 2-4 options (typical mode pattern)
                        if options.len() >= 2 && options.len() <= 4 {
                            return Some(options);
                        }
                    }
                }
                None
            }
            _ => None,
        }
    }

    /// Check if a name is in the skip list
    fn should_skip(&self, name: &str) -> bool {
        self.skip_names.iter().any(|s| s == name)
    }

    /// Check function arguments for flag patterns
    fn check_arguments(
        &self,
        args: &Arguments,
        func_name: &str,
        is_method: bool,
        file_path: &str,
    ) -> Vec<Violation> {
        let mut violations = Vec::new();
        
        // Collect all arguments to check
        let all_args: Vec<_> = args.posonlyargs.iter()
            .chain(args.args.iter())
            .chain(args.kwonlyargs.iter())
            .collect();
        
        for (idx, arg) in all_args.iter().enumerate() {
            let param_name = arg.def.arg.as_str();
            
            // Skip 'self' and 'cls'
            if is_method && idx == 0 && (param_name == "self" || param_name == "cls") {
                continue;
            }
            
            // Skip allowed names
            if self.should_skip(param_name) {
                continue;
            }
            
            // Check for boolean type annotation with flag-like name
            if let Some(annotation) = &arg.def.annotation {
                if Self::is_bool_annotation(annotation) && Self::is_flag_like_name(param_name) {
                    violations.push(Violation::new(
                        "DOEFF011".to_string(),
                        format!(
                            "Function '{}' has flag argument '{}' with type 'bool'. \
                             Accept a callback or protocol object that implements the behavior instead. \
                             Example: instead of 'def process(data, use_cache: bool)', \
                             use 'def process(data, cache: CacheProtocol)' or 'def process(data, get_cached: Callable)'.",
                            func_name, param_name
                        ),
                        arg.def.range.start().to_usize(),
                        file_path.to_string(),
                        Severity::Warning,
                    ));
                    continue;
                }
                
                // Check for Literal mode patterns
                if let Some(options) = Self::is_mode_literal(annotation) {
                    violations.push(Violation::new(
                        "DOEFF011".to_string(),
                        format!(
                            "Function '{}' has mode argument '{}' with options [{}]. \
                             Instead of switching on mode values, accept a callback or protocol object \
                             that implements each behavior variant. \
                             Example: instead of 'mode: Literal[\"fast\", \"safe\"]', \
                             use 'processor: Callable[[Data], Result]' or 'processor: ProcessorProtocol'.",
                            func_name, param_name, options.join(", ")
                        ),
                        arg.def.range.start().to_usize(),
                        file_path.to_string(),
                        Severity::Warning,
                    ));
                    continue;
                }
            }
            
            // Check for flag-like name even without bool annotation
            // (might be using a string mode or similar)
            if Self::is_flag_like_name(param_name) {
                if let Some(annotation) = &arg.def.annotation {
                    // Skip if it's clearly a function/callable (already using strategy pattern)
                    if Self::is_callable_annotation(annotation) {
                        continue;
                    }
                }
                
                // For parameters without annotation or with string/enum-like types
                if arg.def.annotation.is_none() || Self::is_string_or_enum_annotation(arg.def.annotation.as_deref()) {
                    violations.push(Violation::new(
                        "DOEFF011".to_string(),
                        format!(
                            "Function '{}' has flag/mode-like argument '{}'. \
                             Accept a callback or protocol object that implements the varying behavior \
                             instead of a flag that requires internal branching.",
                            func_name, param_name
                        ),
                        arg.def.range.start().to_usize(),
                        file_path.to_string(),
                        Severity::Info,
                    ));
                }
            }
        }
        
        violations
    }

    /// Check if annotation looks like a Callable
    fn is_callable_annotation(expr: &Expr) -> bool {
        match expr {
            Expr::Name(name) => {
                let id = name.id.as_str();
                id == "Callable" || id.ends_with("Strategy") || id.ends_with("Handler")
                    || id.ends_with("Processor") || id.ends_with("Factory")
            }
            Expr::Subscript(subscript) => {
                if let Expr::Name(name) = &*subscript.value {
                    name.id.as_str() == "Callable"
                } else {
                    false
                }
            }
            _ => false,
        }
    }

    /// Check if annotation is str or Enum-like
    fn is_string_or_enum_annotation(expr: Option<&Expr>) -> bool {
        match expr {
            Some(Expr::Name(name)) => {
                let id = name.id.as_str();
                id == "str" || id == "String" || id.ends_with("Mode") || id.ends_with("Type")
            }
            _ => false,
        }
    }

    /// Check if this is a method (has self or cls first param)
    fn is_method(args: &Arguments) -> bool {
        let first = args.posonlyargs.first()
            .or_else(|| args.args.first());
        
        if let Some(arg) = first {
            let name = arg.def.arg.as_str();
            return name == "self" || name == "cls";
        }
        false
    }

    /// Check a regular function definition
    fn check_function(
        &self,
        func: &StmtFunctionDef,
        file_path: &str,
    ) -> Vec<Violation> {
        let is_method = Self::is_method(&func.args);
        self.check_arguments(&func.args, func.name.as_str(), is_method, file_path)
    }

    /// Check an async function definition
    fn check_async_function(
        &self,
        func: &StmtAsyncFunctionDef,
        file_path: &str,
    ) -> Vec<Violation> {
        let is_method = Self::is_method(&func.args);
        self.check_arguments(&func.args, func.name.as_str(), is_method, file_path)
    }

    /// Check dataclass attributes for flag patterns
    fn check_dataclass(
        &self,
        class_def: &StmtClassDef,
        file_path: &str,
    ) -> Vec<Violation> {
        if !has_dataclass_decorator(class_def) {
            return vec![];
        }
        
        let mut violations = Vec::new();
        
        for stmt in &class_def.body {
            if let Stmt::AnnAssign(ann_assign) = stmt {
                if let Expr::Name(name) = &*ann_assign.target {
                    let attr_name = name.id.as_str();
                    
                    if self.should_skip(attr_name) {
                        continue;
                    }
                    
                    // Check for boolean type with flag-like name
                    if Self::is_bool_annotation(&ann_assign.annotation) && Self::is_flag_like_name(attr_name) {
                        violations.push(Violation::new(
                            "DOEFF011".to_string(),
                            format!(
                                "Dataclass '{}' has flag attribute '{}' with type 'bool'. \
                                 Store a protocol object or callable that encapsulates the varying behavior instead. \
                                 Example: instead of 'use_cache: bool = True', \
                                 use 'cache: CacheProtocol = DefaultCache()'.",
                                class_def.name, attr_name
                            ),
                            ann_assign.range.start().to_usize(),
                            file_path.to_string(),
                            Severity::Warning,
                        ));
                        continue;
                    }
                    
                    // Check for Literal mode patterns
                    if let Some(options) = Self::is_mode_literal(&ann_assign.annotation) {
                        violations.push(Violation::new(
                            "DOEFF011".to_string(),
                            format!(
                                "Dataclass '{}' has mode attribute '{}' with options [{}]. \
                                 Store a protocol object or callable that implements \
                                 each behavior variant instead.",
                                class_def.name, attr_name, options.join(", ")
                            ),
                            ann_assign.range.start().to_usize(),
                            file_path.to_string(),
                            Severity::Warning,
                        ));
                    }
                }
            }
        }
        
        violations
    }

}

impl LintRule for NoFlagArgumentsRule {
    fn rule_id(&self) -> &str {
        "DOEFF011"
    }

    fn description(&self) -> &str {
        "Functions and dataclasses should not use flag/mode arguments. Use callbacks or protocol objects instead."
    }

    fn check(&self, context: &RuleContext) -> Vec<Violation> {
        // Check the specific statement type - the framework handles recursive walking
        match context.stmt {
            Stmt::FunctionDef(func) => {
                self.check_function(func, context.file_path)
            }
            Stmt::AsyncFunctionDef(func) => {
                self.check_async_function(func, context.file_path)
            }
            Stmt::ClassDef(class_def) => {
                self.check_dataclass(class_def, context.file_path)
            }
            _ => vec![],
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rustpython_ast::Mod;
    use rustpython_parser::{parse, Mode};

    fn check_stmt_recursive(
        stmt: &Stmt,
        rule: &NoFlagArgumentsRule,
        code: &str,
        ast: &Mod,
        violations: &mut Vec<Violation>,
    ) {
        let context = RuleContext {
            stmt,
            file_path: "test.py",
            source: code,
            ast,
        };
        violations.extend(rule.check(&context));

        // Recursively check nested statements (like the framework does)
        match stmt {
            Stmt::ClassDef(class_def) => {
                for s in &class_def.body {
                    check_stmt_recursive(s, rule, code, ast, violations);
                }
            }
            Stmt::FunctionDef(func) => {
                for s in &func.body {
                    check_stmt_recursive(s, rule, code, ast, violations);
                }
            }
            Stmt::AsyncFunctionDef(func) => {
                for s in &func.body {
                    check_stmt_recursive(s, rule, code, ast, violations);
                }
            }
            _ => {}
        }
    }

    fn check_code(code: &str) -> Vec<Violation> {
        let ast = parse(code, Mode::Module, "test.py").unwrap();
        let rule = NoFlagArgumentsRule::new();
        let mut violations = Vec::new();

        // Check all statements in the module recursively
        if let Mod::Module(module) = &ast {
            for stmt in &module.body {
                check_stmt_recursive(stmt, &rule, code, &ast, &mut violations);
            }
        }

        violations
    }

    #[test]
    fn test_bool_flag_argument() {
        let code = r#"
def process_data(data: list, use_cache: bool = True) -> list:
    if use_cache:
        return get_cached(data)
    return compute(data)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("use_cache"));
        assert!(violations[0].message.contains("callback or protocol"));
    }

    #[test]
    fn test_literal_mode_argument() {
        let code = r#"
from typing import Literal

def sort_items(items: list, mode: Literal["fast", "safe"]) -> list:
    if mode == "fast":
        return quick_sort(items)
    return merge_sort(items)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("mode"));
        assert!(violations[0].message.contains("fast, safe"));
    }

    #[test]
    fn test_flag_like_name_without_annotation() {
        let code = r#"
def fetch_data(url, verbose=False):
    pass
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("verbose"));
    }

    #[test]
    fn test_dataclass_bool_attribute() {
        let code = r#"
from dataclasses import dataclass

@dataclass
class Config:
    name: str
    enable_logging: bool = True
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("enable_logging"));
    }

    #[test]
    fn test_dataclass_literal_attribute() {
        let code = r#"
from dataclasses import dataclass
from typing import Literal

@dataclass
class Settings:
    name: str
    output_format: Literal["json", "xml", "csv"]
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("output_format"));
    }

    #[test]
    fn test_strategy_pattern_allowed() {
        let code = r#"
from typing import Callable

def process_data(data: list, processor: Callable[[list], list]) -> list:
    return processor(data)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_regular_bool_parameter_allowed() {
        let code = r#"
def set_value(enabled: bool) -> None:
    pass
"#;
        // 'enabled' doesn't match our flag patterns (no prefix like is_, use_, etc.)
        // and is a simple state parameter
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_method_with_flag() {
        let code = r#"
class DataProcessor:
    def process(self, data: list, skip_validation: bool = False) -> list:
        if skip_validation:
            return self._process_fast(data)
        return self._process_safe(data)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("skip_validation"));
    }

    #[test]
    fn test_async_function_with_flag() {
        let code = r#"
async def fetch_data(url: str, use_cache: bool = True) -> dict:
    if use_cache:
        return await get_cached(url)
    return await http_get(url)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("use_cache"));
    }

    #[test]
    fn test_optional_bool_flag() {
        let code = r#"
from typing import Optional

def configure(settings: dict, enable_debug: Optional[bool] = None) -> None:
    pass
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("enable_debug"));
    }

    #[test]
    fn test_multiple_flags() {
        let code = r#"
def build(
    source: str,
    use_cache: bool = True,
    enable_minification: bool = False,
    verbose: bool = False,
) -> str:
    pass
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 3);
    }

    #[test]
    fn test_non_dataclass_not_checked() {
        let code = r#"
class RegularClass:
    use_cache: bool = True
"#;
        let violations = check_code(code);
        // Regular classes are not checked for dataclass-specific rules
        // But if there are methods, they would be checked
        assert_eq!(violations.len(), 0);
    }
}


//! DOEFF023: Pipeline Marker Required for Entrypoint @do Functions
//!
//! When a @do decorated function is used to create a module-level Program
//! variable, it must have the `# doeff: pipeline` marker to indicate
//! awareness of pipeline-oriented programming.

use crate::models::{RuleContext, Severity, Violation};
use crate::rules::base::LintRule;
use rustpython_ast::{Expr, Mod, Stmt, StmtAsyncFunctionDef, StmtFunctionDef};
use std::collections::HashMap;

pub struct PipelineMarkerRule;

/// Information about a @do decorated function
struct DoFunctionInfo {
    has_pipeline_marker: bool,
    offset: usize,
}

impl PipelineMarkerRule {
    pub fn new() -> Self {
        Self
    }

    /// Check if the type annotation contains "Program"
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
            Expr::BinOp(binop) => {
                Self::is_program_type(&binop.left) || Self::is_program_type(&binop.right)
            }
            _ => false,
        }
    }

    /// Check if a function has the @do decorator
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

    /// Convert byte offset to line number (0-indexed)
    fn offset_to_line(source: &str, offset: usize) -> usize {
        source[..offset.min(source.len())]
            .chars()
            .filter(|&c| c == '\n')
            .count()
    }

    /// Check if a function has the pipeline marker in surrounding lines
    fn check_marker_in_lines(source: &str, func_offset: usize) -> bool {
        let lines: Vec<&str> = source.lines().collect();
        let func_line = Self::offset_to_line(source, func_offset);

        // Check lines around the function definition (decorator line, def line, and docstring)
        // We need to check a few lines before and after the function start
        let start_check = func_line.saturating_sub(3);
        let end_check = (func_line + 5).min(lines.len());

        for i in start_check..end_check {
            if let Some(line) = lines.get(i) {
                // Check for `# doeff: pipeline` or `# doeff:pipeline` marker
                if line.contains("doeff: pipeline") || line.contains("doeff:pipeline") {
                    return true;
                }
            }
        }

        false
    }

    /// Check if a docstring contains the pipeline marker
    fn check_marker_in_docstring(body: &[Stmt]) -> bool {
        if let Some(first_stmt) = body.first() {
            if let Stmt::Expr(expr_stmt) = first_stmt {
                if let Expr::Constant(constant) = &*expr_stmt.value {
                    if let Some(s) = constant.value.as_str() {
                        if s.contains("doeff: pipeline") || s.contains("doeff:pipeline") {
                            return true;
                        }
                    }
                }
            }
        }
        false
    }

    /// Check if a sync function has the pipeline marker in any of the allowed locations
    fn has_pipeline_marker(func: &StmtFunctionDef, source: &str) -> bool {
        let func_offset = func.range.start().to_usize();
        Self::check_marker_in_lines(source, func_offset)
            || Self::check_marker_in_docstring(&func.body)
    }

    /// Check if an async function has the pipeline marker in any of the allowed locations
    fn has_pipeline_marker_async(func: &StmtAsyncFunctionDef, source: &str) -> bool {
        let func_offset = func.range.start().to_usize();
        Self::check_marker_in_lines(source, func_offset)
            || Self::check_marker_in_docstring(&func.body)
    }

    /// Collect all @do decorated functions in the module (both sync and async)
    fn collect_do_functions(ast: &Mod, source: &str) -> HashMap<String, DoFunctionInfo> {
        let mut do_functions = HashMap::new();

        if let Mod::Module(module) = ast {
            for stmt in &module.body {
                match stmt {
                    Stmt::FunctionDef(func) => {
                        if Self::has_do_decorator(&func.decorator_list) {
                            let has_marker = Self::has_pipeline_marker(func, source);
                            do_functions.insert(
                                func.name.to_string(),
                                DoFunctionInfo {
                                    has_pipeline_marker: has_marker,
                                    offset: func.range.start().to_usize(),
                                },
                            );
                        }
                    }
                    Stmt::AsyncFunctionDef(func) => {
                        if Self::has_do_decorator(&func.decorator_list) {
                            let has_marker = Self::has_pipeline_marker_async(func, source);
                            do_functions.insert(
                                func.name.to_string(),
                                DoFunctionInfo {
                                    has_pipeline_marker: has_marker,
                                    offset: func.range.start().to_usize(),
                                },
                            );
                        }
                    }
                    _ => {}
                }
            }
        }

        do_functions
    }

    /// Extract the function name from a call expression
    fn get_call_func_name(call: &rustpython_ast::ExprCall) -> Option<String> {
        match &*call.func {
            Expr::Name(name) => Some(name.id.to_string()),
            _ => None,
        }
    }

    /// Extract the variable name from the assignment target
    fn get_target_name(expr: &Expr) -> String {
        match expr {
            Expr::Name(name) => name.id.to_string(),
            _ => "<unknown>".to_string(),
        }
    }

    /// Check if this is a test file
    fn is_test_file(file_path: &str) -> bool {
        let path = std::path::Path::new(file_path);
        if let Some(file_name) = path.file_name().and_then(|n| n.to_str()) {
            return file_name.starts_with("test_") || file_name.ends_with("_test.py");
        }
        false
    }
}

impl LintRule for PipelineMarkerRule {
    fn rule_id(&self) -> &str {
        "DOEFF023"
    }

    fn description(&self) -> &str {
        "Pipeline marker required for @do functions creating Program entrypoints"
    }

    fn check(&self, context: &RuleContext) -> Vec<Violation> {
        let mut violations = Vec::new();

        // Skip test files
        if Self::is_test_file(context.file_path) {
            return violations;
        }

        // Only check annotated assignments
        if let Stmt::AnnAssign(ann_assign) = context.stmt {
            // Check if the type annotation is Program or Program[T]
            if !Self::is_program_type(&ann_assign.annotation) {
                return violations;
            }

            // Check if there's a value assigned
            if let Some(value) = &ann_assign.value {
                // Check if the value is a function call
                if let Expr::Call(call) = &**value {
                    // Get the function name being called
                    if let Some(func_name) = Self::get_call_func_name(call) {
                        // Collect all @do functions in the module
                        let do_functions = Self::collect_do_functions(context.ast, context.source);

                        // Check if this is a @do function without the pipeline marker
                        if let Some(do_func_info) = do_functions.get(&func_name) {
                            if !do_func_info.has_pipeline_marker {
                                let var_name = Self::get_target_name(&ann_assign.target);

                                let message = format!(
                                    "@do function '{}' is used to create entrypoint Program '{}' but lacks pipeline marker.\n\n\
                                    Pipeline-oriented programming requires explicit acknowledgment when creating \
                                    Program entrypoints from @do functions.\n\n\
                                    Fix: Add '# doeff: pipeline' marker to the function:\n\n  \
                                    # Option 1: After @do decorator\n  \
                                    @do  # doeff: pipeline\n  \
                                    def {}(...):\n      \
                                    ...\n\n  \
                                    # Option 2: After def line\n  \
                                    @do\n  \
                                    def {}(...):  # doeff: pipeline\n      \
                                    ...\n\n  \
                                    # Option 3: In docstring\n  \
                                    @do\n  \
                                    def {}(...):\n      \
                                    \"\"\"doeff: pipeline\"\"\"\n      \
                                    ...",
                                    func_name, var_name, func_name, func_name, func_name
                                );

                                violations.push(Violation::new(
                                    self.rule_id().to_string(),
                                    message,
                                    do_func_info.offset,
                                    context.file_path.to_string(),
                                    Severity::Warning,
                                ));
                            }
                        }
                    }
                }
            }
        }

        violations
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rustpython_parser::{parse, Mode};

    fn check_code(code: &str) -> Vec<Violation> {
        check_code_with_path(code, "module.py")
    }

    fn check_code_with_path(code: &str, file_path: &str) -> Vec<Violation> {
        let ast = parse(code, Mode::Module, file_path).unwrap();
        let rule = PipelineMarkerRule::new();
        let mut violations = Vec::new();

        if let Mod::Module(module) = &ast {
            for stmt in &module.body {
                let context = RuleContext {
                    stmt,
                    file_path,
                    source: code,
                    ast: &ast,
                };
                violations.extend(rule.check(&context));
            }
        }

        violations
    }

    #[test]
    fn test_do_function_without_marker_violation() {
        let code = r#"
@do
def process_x(data):
    return data

p_result: Program[int] = process_x(p_input)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("process_x"));
        assert!(violations[0].message.contains("p_result"));
        assert!(violations[0].message.contains("doeff: pipeline"));
    }

    #[test]
    fn test_do_function_with_marker_on_do_line() {
        let code = r#"
@do  # doeff: pipeline
def process_x(data):
    return data

p_result: Program[int] = process_x(p_input)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_do_function_with_marker_on_def_line() {
        let code = r#"
@do
def process_x(data):  # doeff: pipeline
    return data

p_result: Program[int] = process_x(p_input)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_do_function_with_marker_in_docstring() {
        let code = r#"
@do
def process_x(data):
    """doeff: pipeline"""
    return data

p_result: Program[int] = process_x(p_input)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_do_function_with_marker_no_space() {
        let code = r#"
@do  # doeff:pipeline
def process_x(data):
    return data

p_result: Program[int] = process_x(p_input)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_non_do_function_ignored() {
        let code = r#"
def regular_func(data):
    return data

p_result: Program[int] = regular_func(p_input)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_non_program_type_ignored() {
        let code = r#"
@do
def process_x(data):
    return data

result: int = process_x(p_input)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_test_file_skipped() {
        let code = r#"
@do
def process_x(data):
    return data

p_result: Program[int] = process_x(p_input)
"#;
        // Test files starting with "test_" should be skipped
        let violations_test = check_code_with_path(code, "test_something.py");
        assert_eq!(violations_test.len(), 0);

        // Test files ending with "_test.py" should be skipped
        let violations_test2 = check_code_with_path(code, "something_test.py");
        assert_eq!(violations_test2.len(), 0);

        // Regular files should NOT be skipped
        let violations_regular = check_code_with_path(code, "module.py");
        assert_eq!(violations_regular.len(), 1);
    }

    #[test]
    fn test_generic_program_type() {
        let code = r#"
@do
def load_data(path):
    return []

p_data: Program[list[dict]] = load_data(path=Path("data.json"))
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("load_data"));
    }

    #[test]
    fn test_multiple_do_functions() {
        let code = r#"
@do  # doeff: pipeline
def marked_func(data):
    return data

@do
def unmarked_func(data):
    return data

p_marked: Program[int] = marked_func(p_input)
p_unmarked: Program[int] = unmarked_func(p_input)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("unmarked_func"));
    }

    #[test]
    fn test_program_union_type() {
        let code = r#"
@do
def process_x(data):
    return data

p_result: Program[int] | None = process_x(p_input)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_do_call_syntax() {
        let code = r#"
@do()
def process_x(data):
    return data

p_result: Program[int] = process_x(p_input)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("process_x"));
    }

    #[test]
    fn test_marker_in_multiline_docstring() {
        let code = r#"
@do
def process_x(data):
    """
    Process data pipeline.
    
    doeff: pipeline
    """
    return data

p_result: Program[int] = process_x(p_input)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_message_contains_all_fix_options() {
        let code = r#"
@do
def process_x(data):
    return data

p_result: Program[int] = process_x(p_input)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        // Check that all three options are mentioned
        assert!(violations[0].message.contains("Option 1"));
        assert!(violations[0].message.contains("Option 2"));
        assert!(violations[0].message.contains("Option 3"));
    }

    #[test]
    fn test_async_do_function_without_marker_violation() {
        let code = r#"
@do
async def async_process(data):
    return data

p_result: Program[int] = async_process(p_input)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("async_process"));
        assert!(violations[0].message.contains("p_result"));
    }

    #[test]
    fn test_async_do_function_with_marker_on_do_line() {
        let code = r#"
@do  # doeff: pipeline
async def async_process(data):
    return data

p_result: Program[int] = async_process(p_input)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_async_do_function_with_marker_on_def_line() {
        let code = r#"
@do
async def async_process(data):  # doeff: pipeline
    return data

p_result: Program[int] = async_process(p_input)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_async_do_function_with_marker_in_docstring() {
        let code = r#"
@do
async def async_process(data):
    """doeff: pipeline"""
    return data

p_result: Program[int] = async_process(p_input)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_mixed_sync_and_async_functions() {
        let code = r#"
@do  # doeff: pipeline
def sync_func(data):
    return data

@do
async def async_func(data):
    return data

p_sync: Program[int] = sync_func(p_input)
p_async: Program[int] = async_func(p_input)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("async_func"));
    }
}


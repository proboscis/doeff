use crate::{
    detect_program_type, EntryCategory, IndexEntry, IndexOutput, Parameter, ProgramTypeKind,
};
use anyhow::Result;
use regex::Regex;
use rustpython_parser::ast::{self, Expr, Stmt};
use std::collections::HashSet;
use std::fs;
use std::path::Path;
use walkdir::WalkDir;

/// Build an index of all Python functions in a directory
pub fn build_index(root_dir: &Path) -> Result<IndexOutput> {
    let mut entries = Vec::new();
    let mut total_files = 0;
    let mut total_functions = 0;

    for entry in WalkDir::new(root_dir)
        .follow_links(true)
        .into_iter()
        .filter_map(|e| e.ok())
    {
        let path = entry.path();
        if path.extension().and_then(|s| s.to_str()) == Some("py") {
            if let Ok(content) = fs::read_to_string(&path) {
                total_files += 1;
                if let Ok(ast) = rustpython_parser::parse(&content, rustpython_parser::Mode::Module, "<file>") {
                    let functions = extract_functions(&ast, &path, &content);
                    total_functions += functions.len();
                    entries.extend(functions);
                }
            }
        }
    }

    Ok(IndexOutput {
        entries,
        total_files,
        total_functions,
    })
}

/// Extract function definitions from an AST
fn extract_functions(ast: &ast::Mod, file_path: &Path, source: &str) -> Vec<IndexEntry> {
    let mut functions = Vec::new();
    
    // Create a mapping from byte offsets to line numbers
    let line_starts = compute_line_starts(source);
    
    match ast {
        ast::Mod::Module(module) => {
            for stmt in &module.body {
                functions.extend(extract_functions_from_stmt(stmt, file_path, source, "", &line_starts));
            }
        }
        _ => {}
    }
    
    functions
}

/// Compute byte offset to line number mapping
fn compute_line_starts(source: &str) -> Vec<usize> {
    let mut line_starts = vec![0];
    for (i, ch) in source.char_indices() {
        if ch == '\n' {
            line_starts.push(i + 1);
        }
    }
    line_starts
}

/// Get line number from byte offset
fn get_line_number(offset: usize, line_starts: &[usize]) -> usize {
    line_starts.iter().position(|&start| start > offset).unwrap_or(line_starts.len())
}

/// Recursively extract functions from statements (handles class methods)
fn extract_functions_from_stmt(stmt: &Stmt, file_path: &Path, source: &str, prefix: &str, line_starts: &[usize]) -> Vec<IndexEntry> {
    let mut functions = Vec::new();
    
    match stmt {
        Stmt::FunctionDef(func) => {
            let entry = create_index_entry(func, file_path, source, prefix, line_starts);
            functions.push(entry);
        }
        Stmt::AsyncFunctionDef(func) => {
            let entry = create_async_index_entry(func, file_path, source, prefix, line_starts);
            functions.push(entry);
        }
        Stmt::ClassDef(class) => {
            let class_prefix = if prefix.is_empty() {
                class.name.to_string()
            } else {
                format!("{}.{}", prefix, class.name)
            };
            
            for stmt in &class.body {
                functions.extend(extract_functions_from_stmt(stmt, file_path, source, &class_prefix, line_starts));
            }
        }
        _ => {}
    }
    
    functions
}

/// Create an IndexEntry from a function definition
fn create_index_entry(
    func: &ast::StmtFunctionDef,
    file_path: &Path,
    source: &str,
    prefix: &str,
    line_starts: &[usize]
) -> IndexEntry {
    let name = if prefix.is_empty() {
        func.name.to_string()
    } else {
        format!("{}.{}", prefix, func.name)
    };
    
    // Extract decorators
    let decorators = extract_decorators(&func.decorator_list);
    
    // Extract parameters
    let parameters = extract_parameters(&func.args);
    
    // Extract return annotation
    let return_annotation = func.returns.as_ref()
        .and_then(|expr| extract_type_annotation(expr));
    
    // Extract markers from trailing comments
    let line_num = get_line_number(func.range.start().to_usize(), line_starts);
    let markers = extract_markers_for_line(source, line_num);
    
    // Extract docstring
    let doc_string = extract_docstring(&func.body);
    
    // Categorize the function
    let categories = categorize_function(
        &parameters,
        &return_annotation,
        &decorators,
        &markers,
    );
    
    // Calculate module path
    let module_path = calculate_module_path(file_path, &name);
    
    IndexEntry {
        name,
        file_path: file_path.to_string_lossy().to_string(),
        line: line_num,
        module_path,
        categories,
        markers,
        decorators,
        all_parameters: parameters,
        return_annotation,
        doc_string,
    }
}

/// Create an IndexEntry from an async function definition
fn create_async_index_entry(
    func: &ast::StmtAsyncFunctionDef,
    file_path: &Path,
    source: &str,
    prefix: &str,
    line_starts: &[usize]
) -> IndexEntry {
    let name = if prefix.is_empty() {
        func.name.to_string()
    } else {
        format!("{}.{}", prefix, func.name)
    };
    
    // Extract decorators
    let decorators = extract_decorators(&func.decorator_list);
    
    // Extract parameters
    let parameters = extract_parameters(&func.args);
    
    // Extract return annotation
    let return_annotation = func.returns.as_ref()
        .and_then(|expr| extract_type_annotation(expr));
    
    // Extract markers from trailing comments
    let line_num = get_line_number(func.range.start().to_usize(), line_starts);
    let markers = extract_markers_for_line(source, line_num);
    
    // Extract docstring
    let doc_string = extract_docstring(&func.body);
    
    // Categorize the function
    let categories = categorize_function(
        &parameters,
        &return_annotation,
        &decorators,
        &markers,
    );
    
    // Calculate module path
    let module_path = calculate_module_path(file_path, &name);
    
    IndexEntry {
        name,
        file_path: file_path.to_string_lossy().to_string(),
        line: line_num,
        module_path,
        categories,
        markers,
        decorators,
        all_parameters: parameters,
        return_annotation,
        doc_string,
    }
}

/// Extract decorator names from decorator list
fn extract_decorators(decorator_list: &[Expr]) -> Vec<String> {
    decorator_list.iter()
        .filter_map(|expr| {
            match expr {
                Expr::Name(name) => Some(name.id.to_string()),
                Expr::Attribute(attr) => Some(attr.attr.to_string()),
                Expr::Call(call) => {
                    match &*call.func {
                        Expr::Name(name) => Some(name.id.to_string()),
                        Expr::Attribute(attr) => Some(attr.attr.to_string()),
                        _ => None
                    }
                }
                _ => None
            }
        })
        .collect()
}

/// Extract parameters from function arguments
fn extract_parameters(args: &ast::Arguments) -> Vec<Parameter> {
    let mut params = Vec::new();
    
    // Process positional arguments
    for arg in &args.args {
        params.push(Parameter {
            name: arg.def.arg.to_string(),
            annotation: arg.def.annotation.as_ref()
                .and_then(|expr| extract_type_annotation(expr)),
            is_required: arg.default.is_none(),
            default: arg.default.as_ref()
                .and_then(|expr| extract_default_value(expr)),
        });
    }
    
    // Process *args
    if let Some(arg) = &args.vararg {
        params.push(Parameter {
            name: format!("*{}", arg.arg),
            annotation: arg.annotation.as_ref()
                .and_then(|expr| extract_type_annotation(expr)),
            is_required: false,
            default: None,
        });
    }
    
    // Process keyword-only arguments
    for arg in &args.kwonlyargs {
        params.push(Parameter {
            name: arg.def.arg.to_string(),
            annotation: arg.def.annotation.as_ref()
                .and_then(|expr| extract_type_annotation(expr)),
            is_required: arg.default.is_none(),
            default: arg.default.as_ref()
                .and_then(|expr| extract_default_value(expr)),
        });
    }
    
    // Process **kwargs
    if let Some(arg) = &args.kwarg {
        params.push(Parameter {
            name: format!("**{}", arg.arg),
            annotation: arg.annotation.as_ref()
                .and_then(|expr| extract_type_annotation(expr)),
            is_required: false,
            default: None,
        });
    }
    
    params
}

/// Extract type annotation from an expression
fn extract_type_annotation(expr: &Expr) -> Option<String> {
    match expr {
        Expr::Name(name) => Some(name.id.to_string()),
        Expr::Subscript(sub) => {
            let base = extract_type_annotation(&sub.value)?;
            let slice = extract_type_annotation(&sub.slice)?;
            Some(format!("{}[{}]", base, slice))
        }
        Expr::Attribute(attr) => {
            let value = extract_type_annotation(&attr.value)?;
            Some(format!("{}.{}", value, attr.attr))
        }
        Expr::Constant(const_) => {
            match &const_.value {
                ast::Constant::Str(s) => Some(s.clone()),
                _ => None
            }
        }
        Expr::Tuple(tuple) => {
            let elements: Vec<String> = tuple.elts.iter()
                .filter_map(|e| extract_type_annotation(e))
                .collect();
            if elements.is_empty() {
                None
            } else {
                Some(elements.join(", "))
            }
        }
        Expr::BinOp(binop) => {
            if let ast::Operator::BitOr = binop.op {
                let left = extract_type_annotation(&binop.left)?;
                let right = extract_type_annotation(&binop.right)?;
                Some(format!("{} | {}", left, right))
            } else {
                None
            }
        }
        _ => None
    }
}

/// Extract default value from an expression
fn extract_default_value(expr: &Expr) -> Option<String> {
    match expr {
        Expr::Constant(const_) => {
            match &const_.value {
                ast::Constant::None => Some("None".to_string()),
                ast::Constant::Bool(b) => Some(b.to_string()),
                ast::Constant::Str(s) => Some(format!("\"{}\"", s)),
                ast::Constant::Int(i) => Some(i.to_string()),
                ast::Constant::Float(f) => Some(f.to_string()),
                _ => None
            }
        }
        Expr::Name(name) => Some(name.id.to_string()),
        Expr::List(list) if list.elts.is_empty() => Some("[]".to_string()),
        Expr::Dict(dict) if dict.keys.is_empty() => Some("{}".to_string()),
        _ => None
    }
}

/// Extract docstring from function body
fn extract_docstring(body: &[Stmt]) -> Option<String> {
    body.first().and_then(|stmt| {
        if let Stmt::Expr(expr_stmt) = stmt {
            if let Expr::Constant(const_) = &*expr_stmt.value {
                if let ast::Constant::Str(s) = &const_.value {
                    return Some(s.clone());
                }
            }
        }
        None
    })
}

/// Extract markers from trailing comments on a line
fn extract_markers_for_line(source: &str, line_num: usize) -> Vec<String> {
    let lines: Vec<&str> = source.lines().collect();
    if line_num > 0 && line_num <= lines.len() {
        let line = lines[line_num - 1];
        extract_markers_from_line(line)
    } else {
        Vec::new()
    }
}

/// Extract markers from a single line
fn extract_markers_from_line(line: &str) -> Vec<String> {
    let re = Regex::new(r"#\s*doeff:\s*(.+)").unwrap();
    re.captures(line)
        .map(|cap| {
            cap[1]
                .split_whitespace()
                .map(|s| s.to_string())
                .collect()
        })
        .unwrap_or_else(Vec::new)
}

/// Categorize a function based on its signature and markers
fn categorize_function(
    parameters: &[Parameter],
    return_annotation: &Option<String>,
    decorators: &[String],
    _markers: &[String],
) -> HashSet<EntryCategory> {
    let mut categories = HashSet::new();
    
    // Check for @do decorator
    let has_do = decorators.iter().any(|d| d == "do");
    if has_do {
        categories.insert(EntryCategory::DoFunction);
    }
    
    // Get first required parameter
    let first_param = parameters.iter().find(|p| p.is_required);
    
    // Check first parameter type
    let first_param_is_program = first_param
        .and_then(|p| p.annotation.as_ref())
        .map(|ann| ann.contains("Program"))
        .unwrap_or(false);
    
    let first_param_is_effect = first_param
        .and_then(|p| p.annotation.as_ref())
        .map(|ann| ann.contains("Effect"))
        .unwrap_or(false);
    
    // Check return type
    let return_kind = return_annotation.as_ref()
        .and_then(|ret| detect_program_type(ret));
    
    // Add parameter-based categories
    if first_param_is_program {
        categories.insert(EntryCategory::AcceptsProgramParam);
    }
    if first_param_is_effect {
        categories.insert(EntryCategory::AcceptsEffectParam);
    }
    
    // Add return-based categories
    match return_kind {
        Some(ProgramTypeKind::Program) => {
            categories.insert(EntryCategory::ReturnsProgram);
        }
        Some(ProgramTypeKind::KleisliProgram) => {
            categories.insert(EntryCategory::ReturnsKleisliProgram);
        }
        _ => {}
    }
    
    // Categorize based on @do decorator
    if has_do {
        if first_param_is_program {
            // @do with Program param -> ProgramTransformer
            categories.insert(EntryCategory::ProgramTransformer);
        } else if first_param_is_effect {
            // @do with Effect param -> Interceptor
            categories.insert(EntryCategory::Interceptor);
        } else {
            // @do with other param -> KleisliProgram
            categories.insert(EntryCategory::KleisliProgram);
        }
    } else {
        // Without @do, use signature-based detection
        if first_param_is_program {
            if matches!(return_kind, Some(ProgramTypeKind::Program)) {
                // Program -> Program = Transformer
                categories.insert(EntryCategory::ProgramTransformer);
            } else {
                // Program -> non-Program = Interpreter
                categories.insert(EntryCategory::ProgramInterpreter);
            }
        } else if first_param_is_effect {
            // Effect -> * = Interceptor
            categories.insert(EntryCategory::Interceptor);
        } else if matches!(return_kind, Some(ProgramTypeKind::Program)) {
            // T -> Program = KleisliProgram
            categories.insert(EntryCategory::KleisliProgram);
        }
    }
    
    categories
}

/// Calculate module path for a function
fn calculate_module_path(file_path: &Path, function_name: &str) -> String {
    // This is a simplified version - in practice, you'd need to handle
    // various Python project structures (setup.py, pyproject.toml, etc.)
    let path_str = file_path.to_string_lossy();
    let path_str = path_str.trim_end_matches(".py");
    let path_str = path_str.replace('/', ".");
    let path_str = path_str.replace('\\', ".");
    
    // Clean up common prefixes
    let path_str = path_str
        .trim_start_matches(".")
        .trim_start_matches("src.")
        .trim_start_matches("lib.");
    
    format!("{}.{}", path_str, function_name)
}

// ============================================================================
// MARKER-BASED FILTERING FUNCTIONS
// ============================================================================

/// Find interpreters (marker-only)
pub fn find_interpreters(entries: &[IndexEntry]) -> Vec<&IndexEntry> {
    entries
        .iter()
        .filter(|entry| {
            entry.markers.iter().any(|m| m.eq_ignore_ascii_case("interpreter"))
        })
        .collect()
}

/// Find transforms (marker-only)
pub fn find_transforms(entries: &[IndexEntry]) -> Vec<&IndexEntry> {
    entries
        .iter()
        .filter(|entry| {
            entry.markers.iter().any(|m| m.eq_ignore_ascii_case("transform"))
        })
        .collect()
}

/// Find Kleisli functions (marker OR @do)
pub fn find_kleisli(entries: &[IndexEntry]) -> Vec<&IndexEntry> {
    entries
        .iter()
        .filter(|entry| {
            // Has kleisli marker
            entry.markers.iter().any(|m| m.eq_ignore_ascii_case("kleisli"))
            // OR is @do decorated AND categorized as KleisliProgram
            || (entry.categories.contains(&EntryCategory::DoFunction) 
                && entry.categories.contains(&EntryCategory::KleisliProgram))
        })
        .collect()
}

/// Find Kleisli functions with type filtering
pub fn find_kleisli_with_type<'a>(entries: &'a [IndexEntry], type_arg: &str) -> Vec<&'a IndexEntry> {
    find_kleisli(entries)
        .into_iter()
        .filter(|entry| {
            // Get first required parameter
            let first_required = entry.all_parameters.iter().find(|p| p.is_required);
            
            if let Some(param) = first_required {
                if let Some(annotation) = &param.annotation {
                    // Check if parameter type matches
                    annotation.contains(type_arg) 
                    || annotation == "Any" 
                    || annotation.contains("typing.Any")
                } else {
                    false
                }
            } else {
                false
            }
        })
        .collect()
}

/// Find interceptors (marker-only)
pub fn find_interceptors(entries: &[IndexEntry]) -> Vec<&IndexEntry> {
    entries
        .iter()
        .filter(|entry| {
            entry.markers.iter().any(|m| m.eq_ignore_ascii_case("interceptor"))
        })
        .collect()
}

/// Find interceptors with type filtering
pub fn find_interceptors_with_type<'a>(entries: &'a [IndexEntry], type_arg: &str) -> Vec<&'a IndexEntry> {
    find_interceptors(entries)
        .into_iter()
        .filter(|entry| {
            // Get first required parameter
            let first_required = entry.all_parameters.iter().find(|p| p.is_required);
            
            if let Some(param) = first_required {
                if let Some(annotation) = &param.annotation {
                    // Check if parameter type matches
                    annotation.contains(type_arg) 
                    || annotation == "Effect" 
                    || annotation.contains("typing.Effect")
                } else {
                    false
                }
            } else {
                false
            }
        })
        .collect()
}
use anyhow::{Context, Result};
use chrono::{DateTime, Utc};
use rustpython_ast::text_size::TextSize;
use rustpython_ast::{
    self as ast, Arg, Arguments, Constant, Expr, Mod, Stmt, StmtAsyncFunctionDef, StmtFunctionDef,
};
use rustpython_parser::{parse, Mode};
use serde::Serialize;
use std::collections::{BTreeMap, HashSet};
use std::fs;
use std::path::Path;
use walkdir::{DirEntry, WalkDir};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum EntryCategory {
    KleisliProgram,
    DoFunction,
    Program,
    ReturnsProgram,
    AcceptsProgramParam,
    AcceptsProgramInterpreterParam,
    ProgramInterpreter,
    ProgramTransformer,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ItemKind {
    Function,
    AsyncFunction,
    Assignment,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ProgramTypeKind {
    Program,
    KleisliProgram,
}

impl ProgramTypeKind {
    fn sort_key(self) -> u8 {
        match self {
            ProgramTypeKind::Program => 0,
            ProgramTypeKind::KleisliProgram => 1,
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct ProgramTypeUsage {
    pub kind: ProgramTypeKind,
    pub raw: String,
    pub type_arguments: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct IndexEntry {
    pub name: String,
    pub qualified_name: String,
    pub file_path: String,
    pub line: usize,
    pub item_kind: ItemKind,
    pub categories: Vec<EntryCategory>,
    pub decorators: Vec<String>,
    pub docstring: Option<String>,
    pub return_annotation: Option<String>,
    pub program_parameters: Vec<ParameterRef>,
    pub program_interpreter_parameters: Vec<ParameterRef>,
    pub type_usages: Vec<ProgramTypeUsage>,
}

#[derive(Debug, Clone, Serialize)]
pub struct ParameterRef {
    pub name: String,
    pub annotation: Option<String>,
    pub is_required: bool,
    pub position: usize,
    pub kind: ParameterKind,
}

#[derive(Debug, Clone, Copy, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ParameterKind {
    PositionalOnly,
    Positional,
    VarArg,
    KeywordOnly,
    VarKeyword,
}

struct ParameterInfo<'a> {
    arg: &'a Arg,
    annotation: Option<&'a Expr>,
    is_required: bool,
    kind: ParameterKind,
    position: usize,
}

#[derive(Debug, Clone, Serialize)]
pub struct IndexStats {
    pub total_entries: usize,
    pub category_counts: BTreeMap<EntryCategory, usize>,
}

#[derive(Debug, Clone, Serialize)]
pub struct Index {
    pub version: String,
    pub root: String,
    pub generated_at: DateTime<Utc>,
    pub entries: Vec<IndexEntry>,
    pub stats: IndexStats,
}

pub fn build_index(root: impl AsRef<Path>) -> Result<Index> {
    let root = root.as_ref();
    let canonical_root = root.canonicalize().unwrap_or_else(|_| root.to_path_buf());

    let mut entries = scan_root(&canonical_root)?;
    entries.sort_by(|a, b| a.qualified_name.cmp(&b.qualified_name));

    let stats = compute_stats(&entries);

    Ok(Index {
        version: "0.1.0".to_string(),
        root: canonical_root.to_string_lossy().to_string(),
        generated_at: Utc::now(),
        entries,
        stats,
    })
}

fn compute_stats(entries: &[IndexEntry]) -> IndexStats {
    let mut counts: BTreeMap<EntryCategory, usize> = BTreeMap::new();
    for entry in entries {
        for category in &entry.categories {
            *counts.entry(*category).or_default() += 1;
        }
    }
    IndexStats {
        total_entries: entries.len(),
        category_counts: counts,
    }
}

fn scan_root(root: &Path) -> Result<Vec<IndexEntry>> {
    let mut entries = Vec::new();
    for entry in WalkDir::new(root)
        .into_iter()
        .filter_entry(|e| should_descend(e))
    {
        let entry = entry?;
        if entry.file_type().is_file() && is_python_file(entry.path()) {
            let mut file_entries = parse_python_file(entry.path(), root)?;
            entries.append(&mut file_entries);
        }
    }
    Ok(entries)
}

pub(crate) fn should_descend(entry: &DirEntry) -> bool {
    if entry.depth() == 0 {
        return true;
    }

    let name = entry.file_name().to_string_lossy();
    const SKIP_DIRS: &[&str] = &[
        ".git",
        "__pycache__",
        "target",
        "tmp",
        "dist",
        "build",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "htmlcov",
        "node_modules",
    ];
    !SKIP_DIRS.iter().any(|skip| *skip == name)
}

pub(crate) fn is_python_file(path: &Path) -> bool {
    matches!(path.extension().and_then(|s| s.to_str()), Some("py"))
}

fn parse_python_file(path: &Path, root: &Path) -> Result<Vec<IndexEntry>> {
    let source =
        fs::read_to_string(path).with_context(|| format!("Failed to read {}", path.display()))?;

    if !source.contains("Program")
        && !source.contains("@do")
        && !source.contains("ProgramInterpreter")
        && !source.contains("KleisliProgram")
    {
        return Ok(Vec::new());
    }

    let source_path = path.to_string_lossy().to_string();
    let module = parse(&source, Mode::Module, &source_path)
        .with_context(|| format!("Failed to parse {}", path.display()))?;

    let module_path = compute_module_path(root, path);
    let line_index = LineIndex::new(&source);

    let mut entries = Vec::new();
    extract_entries(&module, &module_path, path, &line_index, &mut entries);

    Ok(entries)
}

fn extract_entries(
    module: &Mod,
    module_path: &str,
    file_path: &Path,
    line_index: &LineIndex,
    entries: &mut Vec<IndexEntry>,
) {
    let Mod::Module(module) = module else {
        return;
    };

    for stmt in &module.body {
        match stmt {
            Stmt::FunctionDef(func) => {
                if let Some(entry) =
                    analyze_function(func, module_path, file_path, line_index, ItemKind::Function)
                {
                    entries.push(entry);
                }
            }
            Stmt::AsyncFunctionDef(func) => {
                if let Some(entry) = analyze_async_function(
                    func,
                    module_path,
                    file_path,
                    line_index,
                    ItemKind::AsyncFunction,
                ) {
                    entries.push(entry);
                }
            }
            Stmt::Assign(assign) => {
                let mut vars = analyze_assignment(assign, module_path, file_path, line_index);
                entries.append(&mut vars);
            }
            Stmt::AnnAssign(assign) => {
                if let Some(entry) =
                    analyze_ann_assignment(assign, module_path, file_path, line_index)
                {
                    entries.push(entry);
                }
            }
            _ => {}
        }
    }
}

fn analyze_function(
    func: &StmtFunctionDef,
    module_path: &str,
    file_path: &Path,
    line_index: &LineIndex,
    item_kind: ItemKind,
) -> Option<IndexEntry> {
    analyze_callable(
        &func.name,
        &func.decorator_list,
        &func.args,
        func.returns.as_deref(),
        &func.body,
        line_index.line_number(func.range.start()),
        module_path,
        file_path,
        item_kind,
    )
}

fn analyze_async_function(
    func: &StmtAsyncFunctionDef,
    module_path: &str,
    file_path: &Path,
    line_index: &LineIndex,
    item_kind: ItemKind,
) -> Option<IndexEntry> {
    analyze_callable(
        &func.name,
        &func.decorator_list,
        &func.args,
        func.returns.as_deref(),
        &func.body,
        line_index.line_number(func.range.start()),
        module_path,
        file_path,
        item_kind,
    )
}

fn analyze_callable(
    name: &str,
    decorators: &[Expr],
    args: &Arguments,
    returns: Option<&Expr>,
    body: &[Stmt],
    line: usize,
    module_path: &str,
    file_path: &Path,
    item_kind: ItemKind,
) -> Option<IndexEntry> {
    let mut categories: HashSet<EntryCategory> = HashSet::new();

    if decorators.iter().any(|d| is_do_decorator(d)) {
        categories.insert(EntryCategory::DoFunction);
        categories.insert(EntryCategory::KleisliProgram);
    }

    let mut type_usages = Vec::new();
    let parameters = collect_parameter_info(args);

    let mut program_params = Vec::new();
    let mut interpreter_params = Vec::new();

    for param in &parameters {
        if let Some(annotation) = param.annotation {
            collect_program_type_usages(annotation, &mut type_usages);

            if expr_mentions(annotation, &["ProgramInterpreter"]) {
                categories.insert(EntryCategory::AcceptsProgramInterpreterParam);
                interpreter_params.push(ParameterRef {
                    name: param.arg.arg.to_string(),
                    annotation: Some(expr_to_string(annotation)),
                    is_required: param.is_required,
                    position: param.position,
                    kind: param.kind,
                });
                continue;
            }

            if matches!(
                identify_program_kind(annotation),
                Some(ProgramTypeKind::Program)
            ) {
                categories.insert(EntryCategory::AcceptsProgramParam);
                program_params.push(ParameterRef {
                    name: param.arg.arg.to_string(),
                    annotation: Some(expr_to_string(annotation)),
                    is_required: param.is_required,
                    position: param.position,
                    kind: param.kind,
                });
            }
        }
    }

    let first_required = parameters.iter().find(|param| {
        matches!(
            param.kind,
            ParameterKind::PositionalOnly | ParameterKind::Positional
        ) && param.is_required
    });

    let first_param_is_program = first_required
        .and_then(|param| param.annotation)
        .and_then(identify_program_kind)
        .map_or(false, |kind| kind == ProgramTypeKind::Program);

    if first_param_is_program {
        categories.insert(EntryCategory::ProgramInterpreter);
    }

    let mut return_annotation = returns.map(expr_to_string);
    let mut return_kind: Option<ProgramTypeKind> = None;

    if let Some(ret_expr) = returns {
        collect_program_type_usages(ret_expr, &mut type_usages);
        return_kind = identify_program_kind(ret_expr);
        if matches!(
            return_kind,
            Some(ProgramTypeKind::Program) | Some(ProgramTypeKind::KleisliProgram)
        ) || expr_mentions(ret_expr, &["Program", "KleisliProgram"])
        {
            categories.insert(EntryCategory::ReturnsProgram);
        }
        if return_annotation.is_none() {
            return_annotation = Some(expr_to_string(ret_expr));
        }
    }

    if first_param_is_program && matches!(return_kind, Some(ProgramTypeKind::Program)) {
        categories.insert(EntryCategory::ProgramTransformer);
    }

    if categories.is_empty() {
        return None;
    }

    ensure_type_usage_defaults(&mut type_usages, &categories);
    sort_type_usages(&mut type_usages);

    let docstring = extract_docstring(body);
    let decorators = decorators.iter().map(expr_to_string).collect();

    let mut categories_vec: Vec<EntryCategory> = categories.into_iter().collect();
    categories_vec.sort();

    let qualified_name = if module_path.is_empty() {
        name.to_string()
    } else {
        format!("{}.{}", module_path, name)
    };

    Some(IndexEntry {
        name: name.to_string(),
        qualified_name,
        file_path: file_path.to_string_lossy().to_string(),
        line,
        item_kind,
        categories: categories_vec,
        decorators,
        docstring,
        return_annotation,
        program_parameters: program_params,
        program_interpreter_parameters: interpreter_params,
        type_usages,
    })
}

fn analyze_assignment(
    assign: &ast::StmtAssign,
    module_path: &str,
    file_path: &Path,
    line_index: &LineIndex,
) -> Vec<IndexEntry> {
    let mut entries = Vec::new();

    for target in &assign.targets {
        if let Expr::Name(name) = target {
            let mut categories: HashSet<EntryCategory> = HashSet::new();
            let mut type_usages = Vec::new();
            collect_program_type_usages(&assign.value, &mut type_usages);
            for usage in &type_usages {
                match usage.kind {
                    ProgramTypeKind::Program => {
                        categories.insert(EntryCategory::Program);
                    }
                    ProgramTypeKind::KleisliProgram => {
                        categories.insert(EntryCategory::KleisliProgram);
                    }
                }
            }
            if categories.is_empty() {
                continue;
            }

            ensure_type_usage_defaults(&mut type_usages, &categories);
            sort_type_usages(&mut type_usages);

            let qualified_name = if module_path.is_empty() {
                name.id.to_string()
            } else {
                format!("{}.{}", module_path, name.id)
            };
            let mut cats: Vec<EntryCategory> = categories.into_iter().collect();
            cats.sort();

            entries.push(IndexEntry {
                name: name.id.to_string(),
                qualified_name,
                file_path: file_path.to_string_lossy().to_string(),
                line: line_index.line_number(assign.range.start()),
                item_kind: ItemKind::Assignment,
                categories: cats,
                decorators: Vec::new(),
                docstring: None,
                return_annotation: None,
                program_parameters: Vec::new(),
                program_interpreter_parameters: Vec::new(),
                type_usages,
            });
        }
    }

    entries
}

fn analyze_ann_assignment(
    assign: &ast::StmtAnnAssign,
    module_path: &str,
    file_path: &Path,
    line_index: &LineIndex,
) -> Option<IndexEntry> {
    let mut categories = HashSet::new();
    let mut type_usages = Vec::new();

    collect_program_type_usages(assign.annotation.as_ref(), &mut type_usages);
    if let Some(value) = assign.value.as_ref() {
        collect_program_type_usages(value.as_ref(), &mut type_usages);
    }

    for usage in &type_usages {
        match usage.kind {
            ProgramTypeKind::Program => {
                categories.insert(EntryCategory::Program);
            }
            ProgramTypeKind::KleisliProgram => {
                categories.insert(EntryCategory::KleisliProgram);
            }
        }
    }

    if categories.is_empty() {
        return None;
    }

    let Expr::Name(name) = &*assign.target else {
        return None;
    };

    ensure_type_usage_defaults(&mut type_usages, &categories);
    sort_type_usages(&mut type_usages);

    let mut cats: Vec<EntryCategory> = categories.into_iter().collect();
    cats.sort();

    let qualified_name = if module_path.is_empty() {
        name.id.to_string()
    } else {
        format!("{}.{}", module_path, name.id)
    };

    Some(IndexEntry {
        name: name.id.to_string(),
        qualified_name,
        file_path: file_path.to_string_lossy().to_string(),
        line: line_index.line_number(assign.range.start()),
        item_kind: ItemKind::Assignment,
        categories: cats,
        decorators: Vec::new(),
        docstring: None,
        return_annotation: Some(expr_to_string(assign.annotation.as_ref())),
        program_parameters: Vec::new(),
        program_interpreter_parameters: Vec::new(),
        type_usages,
    })
}

fn collect_parameter_info(args: &Arguments) -> Vec<ParameterInfo<'_>> {
    let mut result = Vec::new();
    let mut position: usize = 0;

    for arg in &args.posonlyargs {
        result.push(ParameterInfo {
            arg: &arg.def,
            annotation: arg.def.annotation.as_deref(),
            is_required: arg.default.is_none(),
            kind: ParameterKind::PositionalOnly,
            position,
        });
        position += 1;
    }

    for arg in &args.args {
        result.push(ParameterInfo {
            arg: &arg.def,
            annotation: arg.def.annotation.as_deref(),
            is_required: arg.default.is_none(),
            kind: ParameterKind::Positional,
            position,
        });
        position += 1;
    }

    if let Some(vararg) = args.vararg.as_deref() {
        result.push(ParameterInfo {
            arg: vararg,
            annotation: vararg.annotation.as_deref(),
            is_required: false,
            kind: ParameterKind::VarArg,
            position,
        });
        position += 1;
    }

    for arg in &args.kwonlyargs {
        result.push(ParameterInfo {
            arg: &arg.def,
            annotation: arg.def.annotation.as_deref(),
            is_required: arg.default.is_none(),
            kind: ParameterKind::KeywordOnly,
            position,
        });
        position += 1;
    }

    if let Some(kwarg) = args.kwarg.as_deref() {
        result.push(ParameterInfo {
            arg: kwarg,
            annotation: kwarg.annotation.as_deref(),
            is_required: false,
            kind: ParameterKind::VarKeyword,
            position,
        });
    }

    result
}

pub(crate) fn is_do_decorator(expr: &Expr) -> bool {
    match expr {
        Expr::Name(name) => name.id.as_str() == "do",
        Expr::Attribute(attr) => attr.attr.as_str() == "do" || is_do_decorator(&attr.value),
        Expr::Call(call) => is_do_decorator(&call.func),
        _ => false,
    }
}

fn expr_mentions(expr: &Expr, needles: &[&str]) -> bool {
    if needles.is_empty() {
        return false;
    }
    match expr {
        Expr::Name(name) => needles.iter().any(|needle| name.id.contains(*needle)),
        Expr::Attribute(attr) => {
            needles.iter().any(|needle| attr.attr.contains(*needle))
                || expr_mentions(&attr.value, needles)
        }
        Expr::Subscript(sub) => {
            expr_mentions(&sub.value, needles) || expr_mentions(&sub.slice, needles)
        }
        Expr::Call(call) => {
            expr_mentions(&call.func, needles)
                || call.args.iter().any(|arg| expr_mentions(arg, needles))
                || call
                    .keywords
                    .iter()
                    .any(|kw| expr_mentions(&kw.value, needles))
        }
        Expr::Tuple(tuple) => tuple.elts.iter().any(|elt| expr_mentions(elt, needles)),
        Expr::List(list) => list.elts.iter().any(|elt| expr_mentions(elt, needles)),
        Expr::Constant(constant) => match &constant.value {
            Constant::Str(value) => needles.iter().any(|needle| value.contains(*needle)),
            Constant::Bytes(value) => needles.iter().any(|needle| {
                std::str::from_utf8(value)
                    .map(|s| s.contains(*needle))
                    .unwrap_or(false)
            }),
            Constant::Tuple(values) => values.iter().any(|value| match value {
                Constant::Str(inner) => needles.iter().any(|needle| inner.contains(*needle)),
                _ => false,
            }),
            _ => false,
        },
        Expr::BinOp(binop) => {
            expr_mentions(&binop.left, needles) || expr_mentions(&binop.right, needles)
        }
        Expr::BoolOp(boolop) => boolop
            .values
            .iter()
            .any(|value| expr_mentions(value, needles)),
        Expr::UnaryOp(unary) => expr_mentions(&unary.operand, needles),
        Expr::Compare(compare) => {
            expr_mentions(&compare.left, needles)
                || compare
                    .comparators
                    .iter()
                    .any(|cmp| expr_mentions(cmp, needles))
        }
        Expr::IfExp(ifexp) => {
            expr_mentions(&ifexp.body, needles)
                || expr_mentions(&ifexp.orelse, needles)
                || expr_mentions(&ifexp.test, needles)
        }
        _ => false,
    }
}

pub(crate) fn expr_to_string(expr: &Expr) -> String {
    match expr {
        Expr::Name(name) => name.id.to_string(),
        Expr::Attribute(attr) => {
            let base = expr_to_string(&attr.value);
            if base.is_empty() {
                attr.attr.to_string()
            } else {
                format!("{}.{}", base, attr.attr)
            }
        }
        Expr::Subscript(sub) => {
            let value = expr_to_string(&sub.value);
            let slice = expr_to_string(&sub.slice);
            if slice.is_empty() {
                value
            } else {
                format!("{}[{}]", value, slice)
            }
        }
        Expr::Constant(constant) => match &constant.value {
            Constant::Str(value) => value.clone(),
            Constant::Bytes(value) => match std::str::from_utf8(value) {
                Ok(text) => text.to_string(),
                Err(_) => "<bytes>".to_string(),
            },
            Constant::Int(value) => value.to_string(),
            Constant::Float(value) => value.to_string(),
            Constant::Complex { real, imag } => format!("{}+{}j", real, imag),
            Constant::Bool(value) => value.to_string(),
            Constant::None => "None".to_string(),
            Constant::Ellipsis => "...".to_string(),
            Constant::Tuple(values) => {
                let parts: Vec<String> = values.iter().map(|v| format!("{:?}", v)).collect();
                format!("({})", parts.join(", "))
            }
        },
        Expr::Tuple(tuple) => {
            let parts: Vec<String> = tuple.elts.iter().map(expr_to_string).collect();
            parts.join(", ")
        }
        Expr::List(list) => {
            let parts: Vec<String> = list.elts.iter().map(expr_to_string).collect();
            format!("[{}]", parts.join(", "))
        }
        Expr::Call(call) => expr_to_string(&call.func),
        Expr::BinOp(binop) => {
            let left = expr_to_string(&binop.left);
            let right = expr_to_string(&binop.right);
            format!("{} <op> {}", left, right)
        }
        Expr::IfExp(ifexp) => {
            let body = expr_to_string(&ifexp.body);
            let test = expr_to_string(&ifexp.test);
            let orelse = expr_to_string(&ifexp.orelse);
            format!("{} if {} else {}", body, test, orelse)
        }
        Expr::Lambda(lambda) => {
            let args = lambda
                .args
                .args
                .iter()
                .map(|a| a.def.arg.to_string())
                .collect::<Vec<_>>();
            format!("lambda {}: ...", args.join(", "))
        }
        _ => "".to_string(),
    }
}

fn extract_docstring(body: &[Stmt]) -> Option<String> {
    if let Some(Stmt::Expr(expr_stmt)) = body.first() {
        if let Expr::Constant(constant) = &*expr_stmt.value {
            if let Constant::Str(value) = &constant.value {
                return Some(value.to_string());
            }
        }
    }
    None
}

pub(crate) fn compute_module_path(root: &Path, file_path: &Path) -> String {
    // Try to determine the Python package root
    let package_root = find_python_package_root(root, file_path);
    
    let relative = if let Some(pkg_root) = package_root {
        file_path
            .strip_prefix(&pkg_root)
            .unwrap_or(file_path)
            .to_path_buf()
    } else {
        // Fall back to relative path from root
        file_path
            .strip_prefix(root)
            .unwrap_or(file_path)
            .to_path_buf()
    };

    let mut rel_str = relative.to_string_lossy().replace('\\', "/");

    // Remove .py extension
    if rel_str.ends_with(".py") {
        rel_str.truncate(rel_str.len() - 3);
    }

    // Remove __init__ from the end (package markers)
    if rel_str.ends_with("/__init__") {
        rel_str.truncate(rel_str.len() - "/__init__".len());
    }

    rel_str = rel_str.trim_matches('/').to_string();

    // Convert path separators to dots
    rel_str
        .split('/')
        .filter(|segment| !segment.is_empty())
        .collect::<Vec<_>>()
        .join(".")
}

/// Find the Python package root by looking for package markers
fn find_python_package_root(root: &Path, file_path: &Path) -> Option<std::path::PathBuf> {
    // Walk up from the file to find a Python package root
    let mut current = file_path.parent()?;
    let mut deepest_package = None;
    
    while current.starts_with(root) || current == root {
        // Check if this directory is a Python package
        // A directory is a package if it contains __init__.py OR is listed in pyproject.toml
        let init_py = current.join("__init__.py");
        let pyproject = current.join("pyproject.toml");
        
        if init_py.exists() {
            // This is a Python package
            deepest_package = Some(current.to_path_buf());
        } else if pyproject.exists() {
            // Check if this is the project root with packages
            // For now, we'll assume if there's a pyproject.toml and a subdirectory
            // with the project name, that's the package root
            if let Ok(content) = fs::read_to_string(&pyproject) {
                // Simple heuristic: look for package name in pyproject.toml
                // and check if that directory exists
                if content.contains("name = \"doeff\"") && current.join("doeff").exists() {
                    return Some(current.join("doeff"));
                }
                // Also check common patterns like src/ layout
                if current.join("src").exists() {
                    // Check if src contains packages
                    for entry in fs::read_dir(current.join("src")).ok()? {
                        if let Ok(entry) = entry {
                            let path = entry.path();
                            if path.is_dir() && path.join("__init__.py").exists() {
                                return Some(current.join("src"));
                            }
                        }
                    }
                }
            }
            // If we found pyproject.toml but no package structure, treat this as root
            // Files directly here are not in a package
            return None;
        }
        
        // Move up one directory
        current = current.parent()?;
    }
    
    deepest_package
}

fn push_usage(usages: &mut Vec<ProgramTypeUsage>, usage: ProgramTypeUsage) {
    if !usages.iter().any(|existing| {
        existing.kind == usage.kind
            && existing.raw == usage.raw
            && existing.type_arguments == usage.type_arguments
    }) {
        usages.push(usage);
    }
}

fn ensure_type_usage_defaults(
    type_usages: &mut Vec<ProgramTypeUsage>,
    categories: &HashSet<EntryCategory>,
) {
    if categories.contains(&EntryCategory::Program)
        && !type_usages
            .iter()
            .any(|usage| matches!(usage.kind, ProgramTypeKind::Program))
    {
        push_usage(
            type_usages,
            ProgramTypeUsage {
                kind: ProgramTypeKind::Program,
                raw: "Program".to_string(),
                type_arguments: Vec::new(),
            },
        );
    }

    if categories.contains(&EntryCategory::KleisliProgram)
        && !type_usages
            .iter()
            .any(|usage| matches!(usage.kind, ProgramTypeKind::KleisliProgram))
    {
        push_usage(
            type_usages,
            ProgramTypeUsage {
                kind: ProgramTypeKind::KleisliProgram,
                raw: "KleisliProgram".to_string(),
                type_arguments: Vec::new(),
            },
        );
    }
}

fn sort_type_usages(usages: &mut Vec<ProgramTypeUsage>) {
    usages.sort_by(|a, b| {
        a.kind
            .sort_key()
            .cmp(&b.kind.sort_key())
            .then_with(|| a.raw.cmp(&b.raw))
            .then_with(|| a.type_arguments.cmp(&b.type_arguments))
    });
}

fn collect_program_type_usages(expr: &Expr, usages: &mut Vec<ProgramTypeUsage>) {
    match expr {
        Expr::Subscript(sub) => {
            if let Some(kind) = identify_program_kind(&sub.value) {
                let raw = expr_to_string(expr);
                let args = extract_type_arguments(&sub.slice);
                push_usage(
                    usages,
                    ProgramTypeUsage {
                        kind,
                        raw,
                        type_arguments: args,
                    },
                );
            }
            collect_program_type_usages(&sub.value, usages);
            collect_program_type_usages(&sub.slice, usages);
        }
        Expr::Name(name) => match name.id.as_str() {
            "Program" => push_usage(
                usages,
                ProgramTypeUsage {
                    kind: ProgramTypeKind::Program,
                    raw: "Program".to_string(),
                    type_arguments: Vec::new(),
                },
            ),
            "KleisliProgram" => push_usage(
                usages,
                ProgramTypeUsage {
                    kind: ProgramTypeKind::KleisliProgram,
                    raw: "KleisliProgram".to_string(),
                    type_arguments: Vec::new(),
                },
            ),
            _ => {}
        },
        Expr::Attribute(attr) => {
            if let Some(kind) = identify_program_kind(expr) {
                push_usage(
                    usages,
                    ProgramTypeUsage {
                        kind,
                        raw: expr_to_string(expr),
                        type_arguments: Vec::new(),
                    },
                );
            }
            collect_program_type_usages(&attr.value, usages);
        }
        Expr::Tuple(tuple) => {
            for elt in &tuple.elts {
                collect_program_type_usages(elt, usages);
            }
        }
        Expr::List(list) => {
            for elt in &list.elts {
                collect_program_type_usages(elt, usages);
            }
        }
        Expr::Call(call) => {
            collect_program_type_usages(&call.func, usages);
            for arg in &call.args {
                collect_program_type_usages(arg, usages);
            }
            for keyword in &call.keywords {
                collect_program_type_usages(&keyword.value, usages);
            }
        }
        Expr::BinOp(binop) => {
            collect_program_type_usages(&binop.left, usages);
            collect_program_type_usages(&binop.right, usages);
        }
        Expr::BoolOp(boolop) => {
            for value in &boolop.values {
                collect_program_type_usages(value, usages);
            }
        }
        Expr::UnaryOp(unary) => collect_program_type_usages(&unary.operand, usages),
        Expr::Compare(compare) => {
            collect_program_type_usages(&compare.left, usages);
            for cmp in &compare.comparators {
                collect_program_type_usages(cmp, usages);
            }
        }
        Expr::IfExp(ifexp) => {
            collect_program_type_usages(&ifexp.body, usages);
            collect_program_type_usages(&ifexp.orelse, usages);
            collect_program_type_usages(&ifexp.test, usages);
        }
        _ => {}
    }
}

fn identify_program_kind(expr: &Expr) -> Option<ProgramTypeKind> {
    match expr {
        Expr::Name(name) => match name.id.as_str() {
            "Program" => Some(ProgramTypeKind::Program),
            "KleisliProgram" => Some(ProgramTypeKind::KleisliProgram),
            _ => None,
        },
        Expr::Attribute(attr) => {
            if attr.attr.as_str() == "Program" {
                Some(ProgramTypeKind::Program)
            } else if attr.attr.as_str() == "KleisliProgram" {
                Some(ProgramTypeKind::KleisliProgram)
            } else {
                identify_program_kind(&attr.value)
            }
        }
        Expr::Subscript(sub) => identify_program_kind(&sub.value),
        _ => None,
    }
}

fn extract_type_arguments(slice: &Expr) -> Vec<String> {
    match slice {
        Expr::Tuple(tuple) => tuple.elts.iter().map(expr_to_string).collect(),
        Expr::List(list) => list.elts.iter().map(expr_to_string).collect(),
        Expr::Constant(expr_const) => match &expr_const.value {
            Constant::Tuple(values) => values.iter().map(|value| format!("{:?}", value)).collect(),
            _ => vec![expr_to_string(slice)],
        },
        _ => vec![expr_to_string(slice)],
    }
}

pub fn entry_matches(
    entry: &IndexEntry,
    kind: Option<ProgramTypeKind>,
    type_arg: Option<&str>,
) -> bool {
    let relevant: Vec<&ProgramTypeUsage> = entry
        .type_usages
        .iter()
        .filter(|usage| kind.map_or(true, |target| usage.kind == target))
        .collect();

    if relevant.is_empty() {
        return kind.is_none();
    }

    if let Some(arg) = type_arg {
        let normalized = arg.trim();
        if normalized.is_empty() || normalized.eq_ignore_ascii_case("any") {
            return true;
        }
        relevant.into_iter().any(|usage| {
            if usage.raw.eq_ignore_ascii_case(normalized) {
                return true;
            }
            if usage.type_arguments.is_empty() {
                return true;
            }
            if usage
                .type_arguments
                .iter()
                .any(|candidate| candidate.eq_ignore_ascii_case("any"))
            {
                return true;
            }
            usage
                .type_arguments
                .iter()
                .any(|candidate| candidate == normalized)
        })
    } else {
        true
    }
}

pub(crate) struct LineIndex {
    line_starts: Vec<TextSize>,
}

impl LineIndex {
    pub(crate) fn new(source: &str) -> Self {
        let mut line_starts = vec![TextSize::from(0)];
        for (idx, ch) in source.char_indices() {
            if ch == '\n' {
                line_starts.push(TextSize::from((idx + 1) as u32));
            }
        }
        Self { line_starts }
    }

    pub(crate) fn line_number(&self, offset: TextSize) -> usize {
        self.line_starts
            .binary_search(&offset)
            .unwrap_or_else(|i| i.saturating_sub(1))
            + 1
    }
}

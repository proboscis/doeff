use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{bail, Context, Result};
use rustpython_ast::{self as ast, Expr, Stmt};
use rustpython_parser::{parse, Mode};

use crate::{source, SourceSpan, TargetKind};

#[derive(Debug, Clone)]
pub struct ResolvedTarget {
    pub dotted_path: String,
    pub module: String,
    pub symbol: String,
    pub file_path: PathBuf,
    pub kind: TargetKind,
    pub definition_span: Option<SourceSpan>,
}

pub fn resolve(root: &Path, dotted_path: &str) -> Result<ResolvedTarget> {
    let dotted_path = dotted_path.trim();
    let (module, symbol) = dotted_path
        .rsplit_once('.')
        .context("expected dotted path in the form module.symbol")?;

    let file_path = resolve_module_file(root, module)?;

    let source = fs::read_to_string(&file_path).with_context(|| {
        format!(
            "failed reading source for module '{module}' at '{}'",
            file_path.display()
        )
    })?;

    let module_ast = parse(&source, Mode::Module, &file_path.to_string_lossy())
        .with_context(|| format!("failed to parse module '{}'", module))?;

    let ast::Mod::Module(module_body) = module_ast else {
        bail!("unsupported module type for '{module}'");
    };

    let mut resolved = None;
    for statement in &module_body.body {
        if let Some(info) = classify_statement(statement, symbol, &source, &file_path) {
            resolved = Some(info);
            break;
        }
    }

    let Some((kind, span)) = resolved else {
        bail!("symbol '{symbol}' not found in module '{module}'");
    };

    Ok(ResolvedTarget {
        dotted_path: dotted_path.to_string(),
        module: module.to_string(),
        symbol: symbol.to_string(),
        file_path,
        kind,
        definition_span: Some(span),
    })
}

pub fn resolve_module_file(root: &Path, module: &str) -> Result<PathBuf> {
    let module_path = module.replace('.', "/");
    let mut file_path = root.join(format!("{module_path}.py"));

    if !file_path.exists() {
        let package_init = root.join(&module_path).join("__init__.py");
        if package_init.exists() {
            file_path = package_init;
        }
    }

    if !file_path.exists() {
        bail!(
            "could not resolve module file for '{module}' relative to root '{}'",
            root.display()
        );
    }

    Ok(file_path)
}

fn classify_statement(
    statement: &Stmt,
    symbol: &str,
    source: &str,
    file_path: &Path,
) -> Option<(TargetKind, SourceSpan)> {
    match statement {
        Stmt::FunctionDef(func) => {
            if func.name.as_str() != symbol {
                return None;
            }
            let kind = if func.decorator_list.iter().any(is_do_decorator) {
                TargetKind::KleisliProgram
            } else {
                TargetKind::Other
            };
            let span = span_from_range(func.range, source, file_path);
            Some((kind, span))
        }
        Stmt::AsyncFunctionDef(func) => {
            if func.name.as_str() != symbol {
                return None;
            }
            let kind = if func.decorator_list.iter().any(is_do_decorator) {
                TargetKind::KleisliProgram
            } else {
                TargetKind::Other
            };
            let span = span_from_range(func.range, source, file_path);
            Some((kind, span))
        }
        Stmt::Assign(assign) => {
            if !assign.targets.iter().any(|expr| is_symbol(expr, symbol)) {
                return None;
            }
            let kind = if matches!(assign.value.as_ref(), Expr::Call(_)) {
                TargetKind::ProgramValue
            } else {
                TargetKind::Other
            };
            let span = span_from_range(assign.range, source, file_path);
            Some((kind, span))
        }
        Stmt::AnnAssign(assign) => {
            if !is_symbol(&assign.target, symbol) {
                return None;
            }
            let kind = assign
                .value
                .as_deref()
                .map(|expr| matches!(expr, Expr::Call(_)))
                .map(|is_call| {
                    if is_call {
                        TargetKind::ProgramValue
                    } else {
                        TargetKind::Other
                    }
                })
                .unwrap_or(TargetKind::Other);
            let span = span_from_range(assign.range, source, file_path);
            Some((kind, span))
        }
        _ => None,
    }
}

fn is_symbol(expr: &Expr, symbol: &str) -> bool {
    match expr {
        Expr::Name(name) => name.id.as_str() == symbol,
        _ => false,
    }
}

fn is_do_decorator(expr: &Expr) -> bool {
    match expr {
        Expr::Name(name) => name.id.as_str() == "do",
        Expr::Attribute(attr) => attr.attr.as_str() == "do",
        Expr::Call(call) => is_do_decorator(&call.func),
        _ => false,
    }
}

fn span_from_range(
    range: rustpython_ast::text_size::TextRange,
    source: &str,
    file_path: &Path,
) -> SourceSpan {
    let start = usize::from(range.start());
    let (line, column) = source::line_col_at(source, start);
    SourceSpan {
        file: file_path.to_string_lossy().into_owned(),
        line,
        column,
    }
}

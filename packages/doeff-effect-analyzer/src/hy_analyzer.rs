//! Hy source analyzer: extracts effect usage, function definitions,
//! imports, and call edges from parsed S-expressions.
//!
//! Produces the same `FunctionSummary` / `CallEdge` types as the Python analyzer,
//! allowing the existing cross-module resolution and DAG building to work unchanged.

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

use crate::function_summary::{ArgumentValue, CallArgument, CallEdge, FunctionSummary};
use crate::hy_reader::{parse_hy, span_at, SExpr, SExprKind};
use crate::{EffectUsage, SourceSpan, TargetKind};

/// A Hy module analysis result.
#[derive(Debug, Clone)]
pub struct HyModuleInfo {
    /// All `defk` and `defn` function names defined at top level.
    pub functions: BTreeSet<String>,
    /// Class definitions: class_name → set of method names.
    pub methods: BTreeMap<String, BTreeSet<String>>,
    /// Import map: local_name → ImportInfo.
    pub imports: BTreeMap<String, HyImport>,
    /// Per-function analysis: function_name → HyFunctionInfo.
    pub function_defs: BTreeMap<String, HyFunctionInfo>,
}

#[derive(Debug, Clone)]
pub struct HyImport {
    pub module: String,
    pub symbol: Option<String>,
}

#[derive(Debug, Clone)]
pub struct HyFunctionInfo {
    pub name: String,
    pub kind: TargetKind,
    pub span: SourceSpan,
    pub summary: FunctionSummary,
}

/// Analyze a Hy source file.
pub fn analyze_hy_source(
    source: &str,
    file_path: &Path,
) -> Result<HyModuleInfo, String> {
    let exprs = parse_hy(source).map_err(|e| e.to_string())?;
    let file_str = file_path.to_string_lossy();

    let mut info = HyModuleInfo {
        functions: BTreeSet::new(),
        methods: BTreeMap::new(),
        imports: BTreeMap::new(),
        function_defs: BTreeMap::new(),
    };

    for expr in &exprs {
        let Some(list) = expr.as_list() else {
            continue;
        };
        let Some(head) = list.first().and_then(|e| e.as_symbol()) else {
            continue;
        };

        match head {
            "defk" | "defn" | "defprogram" | "defp" | "defpp" => {
                if let Some(func_info) = analyze_function_def(list, source, &file_str, head) {
                    info.functions.insert(func_info.name.clone());
                    info.function_defs.insert(func_info.name.clone(), func_info);
                }
            }
            "defclass" => {
                analyze_class_def(list, source, &file_str, &mut info);
            }
            "import" => {
                extract_imports(list, &mut info.imports);
            }
            "require" => {
                extract_requires(list, &mut info.imports);
            }
            _ => {}
        }
    }

    Ok(info)
}

/// Analyze a `(defk name [params] ...body...)` or `(defn name [params] ...body...)`.
fn analyze_function_def(
    list: &[SExpr],
    source: &str,
    file: &str,
    head: &str,
) -> Option<HyFunctionInfo> {
    // list[0] = defk/defn, list[1] = name, list[2] = [params], list[3..] = body
    if list.len() < 3 {
        return None;
    }

    let raw_name = list[1].as_symbol()?;
    let name = raw_name.replace('-', "_");
    let span = span_at(source, list[0].start, file);

    // Skip optional docstring and param list to find body
    let body_start = find_body_start(list);
    let body = &list[body_start..];

    let kind = if head == "defk" || head == "defprogram" || head == "defp" || head == "defpp" {
        TargetKind::KleisliProgram
    } else {
        TargetKind::Other
    };

    let mut summary = FunctionSummary {
        symbol: name.clone(),
        ..Default::default()
    };

    for expr in body {
        walk_for_effects_and_calls(expr, source, file, &mut summary);
    }

    Some(HyFunctionInfo {
        name,
        kind,
        span,
        summary,
    })
}

/// Analyze `(defclass Name [Base] ...)` — extract method definitions.
fn analyze_class_def(
    list: &[SExpr],
    source: &str,
    file: &str,
    info: &mut HyModuleInfo,
) {
    if list.len() < 3 {
        return;
    }
    let Some(class_name) = list[1].as_symbol() else {
        return;
    };

    let mut method_names = BTreeSet::new();

    // Walk body forms for defn/defk
    let body_start = if list.get(2).and_then(|e| e.as_vector()).is_some() {
        3
    } else {
        2
    };

    for expr in &list[body_start..] {
        if let Some(items) = expr.as_list() {
            if let Some(head) = items.first().and_then(|e| e.as_symbol()) {
                if (head == "defn" || head == "defk") && items.len() >= 3 {
                    if let Some(method_name) = items[1].as_symbol() {
                        let py_method = method_name.replace('-', "_");
                        method_names.insert(py_method.clone());

                        // Also analyze the method body
                        if let Some(func_info) =
                            analyze_function_def(items, source, file, head)
                        {
                            let qualified =
                                format!("{}.{}", class_name, func_info.name);
                            info.function_defs.insert(qualified, func_info);
                        }
                    }
                }
            }
        }
    }

    if !method_names.is_empty() {
        info.methods.insert(class_name.to_string(), method_names);
    }
}

/// Extract imports from Hy import forms.
///
/// Hy syntax:
///   `(import module)`                           → bare module
///   `(import module [name1 name2])`             → from module import name1, name2
///   `(import module [name1 :as alias])`         → from module import name1 as alias
///   `(import module [name1 :as a name2 :as b])` → multiple aliased
///
/// The items after `import` are pairs: `Symbol Vector` for from-imports,
/// or bare `Symbol` for module imports.
fn extract_imports(list: &[SExpr], imports: &mut BTreeMap<String, HyImport>) {
    if list.len() < 2 {
        return;
    }

    let items = &list[1..];
    let mut i = 0;

    while i < items.len() {
        match &items[i].kind {
            SExprKind::Symbol(module_name) => {
                let module = module_name.replace('-', "_");
                // Check if next item is a vector (from-import names)
                if i + 1 < items.len() {
                    if let Some(names) = items[i + 1].as_vector() {
                        parse_import_names(&module, names, imports);
                        i += 2;
                        continue;
                    }
                    // Check for :as alias
                    let is_as = match &items[i + 1].kind {
                        SExprKind::Keyword(k) => k == "as",
                        _ => false,
                    };
                    if is_as && i + 2 < items.len() {
                        if let Some(alias) = items[i + 2].as_symbol() {
                            imports.insert(
                                alias.replace('-', "_"),
                                HyImport {
                                    module: module.clone(),
                                    symbol: None,
                                },
                            );
                            i += 3;
                            continue;
                        }
                    }
                }
                // Bare module import
                imports.insert(
                    module_name.replace('-', "_"),
                    HyImport {
                        module,
                        symbol: None,
                    },
                );
                i += 1;
            }
            _ => {
                i += 1;
            }
        }
    }
}

/// Parse the `[name1 :as alias name2 :as alias2]` vector in imports.
fn parse_import_names(
    module: &str,
    names: &[SExpr],
    imports: &mut BTreeMap<String, HyImport>,
) {
    let mut i = 0;
    while i < names.len() {
        let Some(name) = names[i].as_symbol() else {
            i += 1;
            continue;
        };

        // Check for :as alias
        let (local_name, skip) = if i + 2 < names.len() {
            let is_as = match &names[i + 1].kind {
                SExprKind::Keyword(k) => k == "as",
                SExprKind::Symbol(s) => s == ":as",
                _ => false,
            };
            if is_as {
                if let Some(alias) = names[i + 2].as_symbol() {
                    (alias, 3)
                } else {
                    (name, 1)
                }
            } else {
                (name, 1)
            }
        } else {
            (name, 1)
        };

        let python_name = name.replace('-', "_");
        let local_python = local_name.replace('-', "_");

        imports.insert(
            local_python,
            HyImport {
                module: module.to_string(),
                symbol: Some(python_name),
            },
        );

        i += skip;
    }
}

/// Extract macro requires — treated similarly to imports for resolution.
/// `(require module [macro1 macro2])`
fn extract_requires(list: &[SExpr], imports: &mut BTreeMap<String, HyImport>) {
    // Same structure as import
    extract_imports(list, imports);
}

/// Find where the body starts (skip name, params, optional docstring).
fn find_body_start(list: &[SExpr]) -> usize {
    // list[0] = defk/defn, list[1] = name
    if list.len() < 3 {
        return list.len();
    }

    let mut idx = 2;
    // Skip param vector
    if list.get(idx).and_then(|e| e.as_vector()).is_some() {
        idx += 1;
    }
    // Skip optional docstring
    if idx < list.len() {
        if let SExprKind::Str(_) = &list[idx].kind {
            idx += 1;
        }
    }
    // Skip optional contracts dict {:pre [...] :post [...]}
    if idx < list.len() {
        if let SExprKind::Dict(_) = &list[idx].kind {
            idx += 1;
        }
    }
    idx
}

/// Walk an S-expression tree to collect effect usages and call edges.
fn walk_for_effects_and_calls(
    expr: &SExpr,
    source: &str,
    file: &str,
    summary: &mut FunctionSummary,
) {
    let Some(list) = expr.as_list() else {
        // Not a list — nothing to extract
        return;
    };

    if list.is_empty() {
        return;
    }

    let Some(head) = list[0].as_symbol() else {
        // Head is not a symbol — recurse into sub-expressions
        for item in list {
            walk_for_effects_and_calls(item, source, file, summary);
        }
        return;
    };

    match head {
        "<-" => {
            // Effect binding: (<- name (Effect ...)) or (<- (Effect ...))
            extract_effect_from_bind(list, source, file, summary);
        }
        "!" => {
            // Bang syntax: (! expr) — inline bind, equivalent to (<- _tmp expr)
            if list.len() >= 2 {
                // Treat as a 2-element bind: (<- (expr))
                let pseudo_bind = [list[0].clone(), list[1].clone()];
                extract_effect_from_bind(&pseudo_bind, source, file, summary);
            }
        }
        "traverse" => {
            // traverse macro body — walk inner forms
            for item in &list[1..] {
                walk_for_effects_and_calls(item, source, file, summary);
            }
        }
        "fold" => {
            // (fold collection :init v body) — walk body
            for item in &list[1..] {
                walk_for_effects_and_calls(item, source, file, summary);
            }
        }
        "import" | "require" => {
            // Import statement inside function body — skip (not a call)
        }
        "do" | "let" | "when" | "if" | "cond" | "setv" | "return" => {
            // Control flow — recurse
            for item in &list[1..] {
                walk_for_effects_and_calls(item, source, file, summary);
            }
        }
        "defk" | "defn" | "fn" | "fnk" => {
            // Nested function definition — don't recurse into it
            // (its effects belong to the inner scope, not this function)
        }
        "yield" => {
            // Bare yield — check if it's yielding an effect
            if list.len() >= 2 {
                if let Some(inner_list) = list[1].as_list() {
                    if let Some(effect_name) = inner_list.first().and_then(|e| e.as_symbol()) {
                        if looks_like_effect(effect_name) {
                            let eu = make_effect_usage(effect_name, inner_list, source, file);
                            summary.local_effects.push(eu);
                        }
                    }
                }
            }
        }
        _ => {
            // Could be a function call
            if looks_like_call(head) {
                let call = make_call_edge(head, list, source, file);
                summary.calls.push(call);
            }
            // Recurse into arguments
            for item in &list[1..] {
                walk_for_effects_and_calls(item, source, file, summary);
            }
        }
    }
}

/// Extract effect from `(<- name (Effect ...))` or `(<- (Effect ...))`.
fn extract_effect_from_bind(
    list: &[SExpr],
    source: &str,
    file: &str,
    summary: &mut FunctionSummary,
) {
    // (<- name (Effect ...)) — 3 elements
    // (<- name Type (Effect ...)) — 4 elements (typed bind)
    // (<- (Effect ...)) — 2 elements (unbound)
    let effect_expr = if list.len() == 2 {
        // (<- (Effect ...))
        &list[1]
    } else if list.len() == 3 {
        // (<- name (Effect ...))
        &list[2]
    } else if list.len() == 4 {
        // (<- name Type (Effect ...))
        &list[3]
    } else {
        return;
    };

    // The effect expression might be a direct effect constructor or a function call
    if let Some(inner_list) = effect_expr.as_list() {
        if let Some(callee) = inner_list.first().and_then(|e| e.as_symbol()) {
            if looks_like_effect(callee) {
                let eu = make_effect_usage(callee, inner_list, source, file);
                summary.local_effects.push(eu);
            } else {
                // It's a function call that returns a value
                let call = make_call_edge(callee, inner_list, source, file);
                summary.calls.push(call);
            }

            // Also recurse into arguments (they might contain nested effects)
            for item in &inner_list[1..] {
                walk_for_effects_and_calls(item, source, file, summary);
            }
        }
    }

    // Recurse into any other parts
    for item in &list[1..] {
        if !std::ptr::eq(item, effect_expr) {
            walk_for_effects_and_calls(item, source, file, summary);
        }
    }
}

/// Create an EffectUsage from an effect call.
fn make_effect_usage(
    name: &str,
    list: &[SExpr],
    source: &str,
    file: &str,
) -> EffectUsage {
    let span = span_at(source, list[0].start, file);

    // For Ask, try to extract the literal key
    let key = if name == "Ask" {
        if let Some(key_str) = list.get(1).and_then(|e| e.as_str()) {
            format!("ask:{}", key_str)
        } else {
            "ask:<dynamic>".to_string()
        }
    } else {
        name.replace('-', "_")
    };

    EffectUsage {
        key,
        span: Some(span),
        via: None,
    }
}

/// Create a CallEdge from a function call.
fn make_call_edge(callee: &str, list: &[SExpr], source: &str, file: &str) -> CallEdge {
    let span = span_at(source, list[0].start, file);
    let python_callee = callee.replace('-', "_");

    let mut arguments = Vec::new();
    let mut i = 1;
    while i < list.len() {
        // Check for keyword argument :key value
        if let SExprKind::Keyword(kw) = &list[i].kind {
            if i + 1 < list.len() {
                arguments.push(CallArgument {
                    name: Some(kw.clone()),
                    value: argument_value_from_sexpr(&list[i + 1]),
                });
                i += 2;
                continue;
            }
        }
        arguments.push(CallArgument {
            name: None,
            value: argument_value_from_sexpr(&list[i]),
        });
        i += 1;
    }

    CallEdge {
        label: format!("({})", list.iter().take(3).map(|e| sexpr_short_repr(e)).collect::<Vec<_>>().join(" ")),
        span,
        callee: Some(python_callee),
        object: None,
        extra_callees: Vec::new(),
        arguments,
    }
}

fn argument_value_from_sexpr(expr: &SExpr) -> ArgumentValue {
    match &expr.kind {
        SExprKind::Symbol(s) => ArgumentValue::Identifier(s.replace('-', "_")),
        SExprKind::Str(s) => ArgumentValue::Other(format!("\"{}\"", s)),
        SExprKind::Number(n) => ArgumentValue::Other(n.clone()),
        SExprKind::List(items) => {
            if let Some(callee) = items.first().and_then(|e| e.as_symbol()) {
                ArgumentValue::Call(callee.replace('-', "_"))
            } else {
                ArgumentValue::Other("<expr>".to_string())
            }
        }
        _ => ArgumentValue::Other("<expr>".to_string()),
    }
}

fn sexpr_short_repr(expr: &SExpr) -> String {
    match &expr.kind {
        SExprKind::Symbol(s) => s.clone(),
        SExprKind::Str(s) => format!("\"{}\"", s),
        SExprKind::Number(n) => n.clone(),
        SExprKind::Keyword(k) => format!(":{}", k),
        SExprKind::List(_) => "(...)".to_string(),
        SExprKind::Vector(_) => "[...]".to_string(),
        _ => "...".to_string(),
    }
}

/// Heuristic: does this name look like an effect constructor?
/// Effect names are PascalCase (start with uppercase).
fn looks_like_effect(name: &str) -> bool {
    name.chars()
        .next()
        .map(|c| c.is_uppercase())
        .unwrap_or(false)
}

/// Heuristic: does this name look like a function call (not a special form)?
fn looks_like_call(name: &str) -> bool {
    // Skip known Hy special forms and macros
    !matches!(
        name,
        "if" | "do" | "let" | "when" | "unless" | "cond" | "setv" | "return"
            | "for" | "while" | "try" | "except" | "raise" | "assert"
            | "print" | "isinstance" | "len" | "list" | "dict" | "set"
            | "get" | "str" | "int" | "float" | "round" | "range"
            | "not" | "and" | "or" | "in" | "is"
            | "+" | "-" | "*" | "/" | ">" | "<" | ">=" | "<=" | "=" | "!="
            | "import" | "require" | "export"
    ) && !name.starts_with('.')
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn analyze(source: &str) -> HyModuleInfo {
        analyze_hy_source(source, &PathBuf::from("test.hy")).unwrap()
    }

    #[test]
    fn test_defk_extraction() {
        let info = analyze(r#"
(defk pipeline [items]
  (<- model (Ask "model"))
  (<- result (Compute model))
  result)
"#);
        assert!(info.functions.contains("pipeline"));
        let func = &info.function_defs["pipeline"];
        assert_eq!(func.kind, TargetKind::KleisliProgram);
        // Should have Ask and Compute effects
        let effect_keys: Vec<_> = func.summary.local_effects.iter().map(|e| &e.key).collect();
        assert!(effect_keys.iter().any(|k| k.starts_with("ask:")));
        assert!(effect_keys.iter().any(|k| k.contains("Compute")));
    }

    #[test]
    fn test_function_call_extraction() {
        let info = analyze(r#"
(defk pipeline [items]
  (<- result (fetch-data items))
  result)
"#);
        let func = &info.function_defs["pipeline"];
        assert!(!func.summary.calls.is_empty());
        assert_eq!(
            func.summary.calls[0].callee.as_deref(),
            Some("fetch_data")
        );
    }

    #[test]
    fn test_import_extraction() {
        let info = analyze(r#"
(import doeff [run EffectBase])
(import doeff-traverse.handlers [sequential parallel])
"#);
        assert!(info.imports.contains_key("run"));
        assert!(info.imports.contains_key("EffectBase"));
        assert!(info.imports.contains_key("sequential"));
        assert!(info.imports.contains_key("parallel"));
    }

    #[test]
    fn test_import_with_alias() {
        let info = analyze(r#"
(import doeff_core_effects [try-handler :as try_handler])
"#);
        assert!(info.imports.contains_key("try_handler"));
        let imp = &info.imports["try_handler"];
        assert_eq!(imp.module, "doeff_core_effects");
        assert_eq!(imp.symbol.as_deref(), Some("try_handler"));
    }

    #[test]
    fn test_ask_key_extraction() {
        let info = analyze(r#"
(defk pipeline []
  (<- model (Ask "gpt-4o"))
  (<- x (Ask dynamic-key))
  model)
"#);
        let func = &info.function_defs["pipeline"];
        let effect_keys: Vec<_> = func.summary.local_effects.iter().map(|e| &e.key).collect();
        assert!(effect_keys.iter().any(|k| *k == "ask:gpt-4o"));
        assert!(effect_keys.iter().any(|k| *k == "ask:<dynamic>"));
    }

    #[test]
    fn test_defclass_extraction() {
        let info = analyze(r#"
(defclass Compute [EffectBase]
  (defn __init__ [self x]
    (.__init__ (super))
    (setv self.x x)))
"#);
        assert!(info.methods.contains_key("Compute"));
        assert!(info.methods["Compute"].contains("__init__"));
    }
}

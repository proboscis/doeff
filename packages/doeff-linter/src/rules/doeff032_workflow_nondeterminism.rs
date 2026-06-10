//! DOEFF032: Workflow Glue Nondeterminism
//!
//! Workflow modules must keep glue deterministic. Raw clock, random, I/O,
//! subprocess, network, and non-allowlisted imports must cross explicit DSL
//! effect boundaries instead.

use crate::models::{RuleContext, Severity, Violation};
use crate::rules::base::LintRule;
use rustpython_ast::{Expr, Mod, Stmt};
use std::collections::HashMap;
use std::path::Path;

const RULE_ID: &str = "DOEFF032";

const ALLOWLISTED_IMPORTS: &[&str] = &[
    "__future__",
    "abc",
    "collections",
    "dataclasses",
    "decimal",
    "doeff",
    "doeff_conductor",
    "enum",
    "fractions",
    "functools",
    "itertools",
    "json",
    "math",
    "operator",
    "pydantic",
    "pydantic_core",
    "re",
    "types",
    "typing",
    "typing_extensions",
];

const NETWORK_MODULES: &[&str] = &["requests", "httpx", "socket", "urllib"];
const PATHLIB_WRITE_METHODS: &[&str] = &[
    "chmod",
    "mkdir",
    "open",
    "rename",
    "replace",
    "rmdir",
    "symlink_to",
    "touch",
    "unlink",
    "write_bytes",
    "write_text",
];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Replacement {
    Time,
    Random,
    Gate,
    Params,
}

impl Replacement {
    fn token(self) -> &'static str {
        match self {
            Replacement::Time => "time!",
            Replacement::Random => "random!",
            Replacement::Gate => "gate!",
            Replacement::Params => ":params",
        }
    }

    fn suggestion(self) -> &'static str {
        match self {
            Replacement::Time => "use the explicit `time!` workflow effect",
            Replacement::Random => "use the explicit `random!` workflow effect",
            Replacement::Gate => "move the operation behind a deterministic `gate!` step",
            Replacement::Params => "pass the value through a workflow `:params` entry",
        }
    }
}

pub struct WorkflowNondeterminismRule;

impl WorkflowNondeterminismRule {
    pub fn new() -> Self {
        Self
    }

    fn is_workflow_module(file_path: &str, source: &str) -> bool {
        if source
            .lines()
            .take(20)
            .any(|line| line.contains("doeff: workflow") || line.contains("doeff:workflow"))
        {
            return true;
        }

        let path = Path::new(file_path);
        let has_workflows_component = path
            .components()
            .filter_map(|component| component.as_os_str().to_str())
            .any(|component| component == "workflows");
        let has_workflow_suffix = path
            .file_name()
            .and_then(|name| name.to_str())
            .map_or(false, |name| name.ends_with("_workflow.py"));

        has_workflows_component || has_workflow_suffix
    }

    fn root_module(module: &str) -> &str {
        module.split('.').next().unwrap_or(module)
    }

    fn is_module_or_child(module: &str, root: &str) -> bool {
        module == root || module.starts_with(&format!("{root}."))
    }

    fn is_allowlisted_import(module: &str) -> bool {
        ALLOWLISTED_IMPORTS
            .iter()
            .any(|allowed| Self::is_module_or_child(module, allowed))
    }

    fn classify_import(module: &str) -> Option<Replacement> {
        let root = Self::root_module(module);

        if root == "datetime" || root == "time" {
            Some(Replacement::Time)
        } else if root == "random" {
            Some(Replacement::Random)
        } else if root == "pathlib" || root == "subprocess" {
            Some(Replacement::Gate)
        } else if NETWORK_MODULES.contains(&root) {
            Some(Replacement::Gate)
        } else if Self::is_allowlisted_import(module) {
            None
        } else {
            Some(Replacement::Params)
        }
    }

    fn collect_import_aliases(ast: &Mod) -> HashMap<String, String> {
        let mut aliases = HashMap::new();

        if let Mod::Module(module) = ast {
            for stmt in &module.body {
                match stmt {
                    Stmt::Import(import) => {
                        for alias in &import.names {
                            let module_name = alias.name.as_str();
                            let local_name = alias
                                .asname
                                .as_ref()
                                .map(|name| name.as_str())
                                .unwrap_or_else(|| Self::root_module(module_name));
                            let resolved = if alias.asname.is_some() {
                                module_name
                            } else {
                                Self::root_module(module_name)
                            };
                            aliases.insert(local_name.to_string(), resolved.to_string());
                        }
                    }
                    Stmt::ImportFrom(import_from) => {
                        let Some(module_name) = import_from.module.as_ref().map(|m| m.as_str())
                        else {
                            continue;
                        };

                        for alias in &import_from.names {
                            let imported_name = alias.name.as_str();
                            if imported_name == "*" {
                                continue;
                            }
                            let local_name = alias
                                .asname
                                .as_ref()
                                .map(|name| name.as_str())
                                .unwrap_or(imported_name);
                            aliases.insert(
                                local_name.to_string(),
                                format!("{module_name}.{imported_name}"),
                            );
                        }
                    }
                    _ => {}
                }
            }
        }

        aliases
    }

    fn check_import(stmt: &Stmt, violations: &mut Vec<Violation>, file_path: &str) {
        match stmt {
            Stmt::Import(import) => {
                for alias in &import.names {
                    let module_name = alias.name.as_str();
                    if let Some(replacement) = Self::classify_import(module_name) {
                        violations.push(Self::create_violation(
                            format!("import `{module_name}`"),
                            replacement,
                            import.range.start().to_usize(),
                            file_path,
                        ));
                    }
                }
            }
            Stmt::ImportFrom(import_from) => {
                let module_name = import_from
                    .module
                    .as_ref()
                    .map(|module| module.as_str())
                    .unwrap_or("<relative>");

                if let Some(replacement) = Self::classify_import(module_name) {
                    violations.push(Self::create_violation(
                        format!("import from `{module_name}`"),
                        replacement,
                        import_from.range.start().to_usize(),
                        file_path,
                    ));
                }
            }
            _ => {}
        }
    }

    fn resolve_expr_path(expr: &Expr, aliases: &HashMap<String, String>) -> Option<String> {
        match expr {
            Expr::Name(name) => Some(
                aliases
                    .get(name.id.as_str())
                    .cloned()
                    .unwrap_or_else(|| name.id.to_string()),
            ),
            Expr::Attribute(attr) => {
                let base = Self::resolve_expr_path(&attr.value, aliases)?;
                Some(format!("{}.{}", base, attr.attr))
            }
            Expr::Call(call) => Self::resolve_expr_path(&call.func, aliases),
            Expr::Subscript(subscript) => Self::resolve_expr_path(&subscript.value, aliases),
            _ => None,
        }
    }

    fn classify_call(path: &str) -> Option<Replacement> {
        match path {
            "datetime.now"
            | "datetime.today"
            | "datetime.datetime.now"
            | "datetime.datetime.today"
            | "time.time"
            | "time.monotonic" => Some(Replacement::Time),
            "open" => Some(Replacement::Gate),
            _ if path.starts_with("random.") => Some(Replacement::Random),
            _ if path.starts_with("subprocess.") => Some(Replacement::Gate),
            _ if NETWORK_MODULES
                .iter()
                .any(|module| Self::is_module_or_child(path, module)) =>
            {
                Some(Replacement::Gate)
            }
            _ if Self::is_pathlib_write_call(path) => Some(Replacement::Gate),
            _ => None,
        }
    }

    fn is_pathlib_write_call(path: &str) -> bool {
        let Some(method_name) = path.rsplit('.').next() else {
            return false;
        };

        PATHLIB_WRITE_METHODS.contains(&method_name)
            && (path.starts_with("pathlib.Path.")
                || path.starts_with("pathlib.PosixPath.")
                || path.starts_with("pathlib.WindowsPath.")
                || path.starts_with("Path.")
                || path.starts_with("PosixPath.")
                || path.starts_with("WindowsPath."))
    }

    fn check_expr(
        expr: &Expr,
        aliases: &HashMap<String, String>,
        violations: &mut Vec<Violation>,
        file_path: &str,
    ) {
        if let Expr::Call(call) = expr {
            if let Some(path) = Self::resolve_expr_path(&call.func, aliases) {
                if let Some(replacement) = Self::classify_call(&path) {
                    violations.push(Self::create_violation(
                        format!("call `{path}`"),
                        replacement,
                        call.range.start().to_usize(),
                        file_path,
                    ));
                }
            }
        }

        Self::check_nested_exprs(expr, aliases, violations, file_path);
    }

    fn check_nested_exprs(
        expr: &Expr,
        aliases: &HashMap<String, String>,
        violations: &mut Vec<Violation>,
        file_path: &str,
    ) {
        match expr {
            Expr::BoolOp(boolop) => {
                for value in &boolop.values {
                    Self::check_expr(value, aliases, violations, file_path);
                }
            }
            Expr::NamedExpr(named) => {
                Self::check_expr(&named.value, aliases, violations, file_path);
            }
            Expr::BinOp(binop) => {
                Self::check_expr(&binop.left, aliases, violations, file_path);
                Self::check_expr(&binop.right, aliases, violations, file_path);
            }
            Expr::UnaryOp(unaryop) => {
                Self::check_expr(&unaryop.operand, aliases, violations, file_path);
            }
            Expr::Lambda(lambda) => {
                Self::check_expr(&lambda.body, aliases, violations, file_path);
            }
            Expr::IfExp(ifexp) => {
                Self::check_expr(&ifexp.test, aliases, violations, file_path);
                Self::check_expr(&ifexp.body, aliases, violations, file_path);
                Self::check_expr(&ifexp.orelse, aliases, violations, file_path);
            }
            Expr::Dict(dict) => {
                for key in dict.keys.iter().flatten() {
                    Self::check_expr(key, aliases, violations, file_path);
                }
                for value in &dict.values {
                    Self::check_expr(value, aliases, violations, file_path);
                }
            }
            Expr::Set(set) => {
                for elt in &set.elts {
                    Self::check_expr(elt, aliases, violations, file_path);
                }
            }
            Expr::ListComp(listcomp) => {
                Self::check_expr(&listcomp.elt, aliases, violations, file_path);
            }
            Expr::SetComp(setcomp) => {
                Self::check_expr(&setcomp.elt, aliases, violations, file_path);
            }
            Expr::DictComp(dictcomp) => {
                Self::check_expr(&dictcomp.key, aliases, violations, file_path);
                Self::check_expr(&dictcomp.value, aliases, violations, file_path);
            }
            Expr::GeneratorExp(genexp) => {
                Self::check_expr(&genexp.elt, aliases, violations, file_path);
            }
            Expr::Await(await_expr) => {
                Self::check_expr(&await_expr.value, aliases, violations, file_path);
            }
            Expr::Yield(yield_expr) => {
                if let Some(value) = &yield_expr.value {
                    Self::check_expr(value, aliases, violations, file_path);
                }
            }
            Expr::YieldFrom(yieldfrom) => {
                Self::check_expr(&yieldfrom.value, aliases, violations, file_path);
            }
            Expr::Compare(compare) => {
                Self::check_expr(&compare.left, aliases, violations, file_path);
                for comparator in &compare.comparators {
                    Self::check_expr(comparator, aliases, violations, file_path);
                }
            }
            Expr::Call(call) => {
                Self::check_expr(&call.func, aliases, violations, file_path);
                for arg in &call.args {
                    Self::check_expr(arg, aliases, violations, file_path);
                }
                for keyword in &call.keywords {
                    Self::check_expr(&keyword.value, aliases, violations, file_path);
                }
            }
            Expr::FormattedValue(formatted) => {
                Self::check_expr(&formatted.value, aliases, violations, file_path);
            }
            Expr::JoinedStr(joined) => {
                for value in &joined.values {
                    Self::check_expr(value, aliases, violations, file_path);
                }
            }
            Expr::Attribute(attribute) => {
                Self::check_expr(&attribute.value, aliases, violations, file_path);
            }
            Expr::Subscript(subscript) => {
                Self::check_expr(&subscript.value, aliases, violations, file_path);
                Self::check_expr(&subscript.slice, aliases, violations, file_path);
            }
            Expr::Starred(starred) => {
                Self::check_expr(&starred.value, aliases, violations, file_path);
            }
            Expr::List(list) => {
                for elt in &list.elts {
                    Self::check_expr(elt, aliases, violations, file_path);
                }
            }
            Expr::Tuple(tuple) => {
                for elt in &tuple.elts {
                    Self::check_expr(elt, aliases, violations, file_path);
                }
            }
            Expr::Slice(slice) => {
                if let Some(lower) = &slice.lower {
                    Self::check_expr(lower, aliases, violations, file_path);
                }
                if let Some(upper) = &slice.upper {
                    Self::check_expr(upper, aliases, violations, file_path);
                }
                if let Some(step) = &slice.step {
                    Self::check_expr(step, aliases, violations, file_path);
                }
            }
            _ => {}
        }
    }

    fn check_stmt_exprs(
        stmt: &Stmt,
        aliases: &HashMap<String, String>,
        violations: &mut Vec<Violation>,
        file_path: &str,
    ) {
        match stmt {
            Stmt::Expr(expr_stmt) => {
                Self::check_expr(&expr_stmt.value, aliases, violations, file_path);
            }
            Stmt::Assign(assign) => {
                Self::check_expr(&assign.value, aliases, violations, file_path);
            }
            Stmt::AnnAssign(ann_assign) => {
                if let Some(value) = &ann_assign.value {
                    Self::check_expr(value, aliases, violations, file_path);
                }
            }
            Stmt::AugAssign(aug_assign) => {
                Self::check_expr(&aug_assign.value, aliases, violations, file_path);
            }
            Stmt::Return(return_stmt) => {
                if let Some(value) = &return_stmt.value {
                    Self::check_expr(value, aliases, violations, file_path);
                }
            }
            Stmt::If(if_stmt) => {
                Self::check_expr(&if_stmt.test, aliases, violations, file_path);
            }
            Stmt::While(while_stmt) => {
                Self::check_expr(&while_stmt.test, aliases, violations, file_path);
            }
            Stmt::For(for_stmt) => {
                Self::check_expr(&for_stmt.iter, aliases, violations, file_path);
            }
            Stmt::With(with_stmt) => {
                for item in &with_stmt.items {
                    Self::check_expr(&item.context_expr, aliases, violations, file_path);
                }
            }
            Stmt::Assert(assert_stmt) => {
                Self::check_expr(&assert_stmt.test, aliases, violations, file_path);
                if let Some(msg) = &assert_stmt.msg {
                    Self::check_expr(msg, aliases, violations, file_path);
                }
            }
            Stmt::Raise(raise_stmt) => {
                if let Some(exc) = &raise_stmt.exc {
                    Self::check_expr(exc, aliases, violations, file_path);
                }
                if let Some(cause) = &raise_stmt.cause {
                    Self::check_expr(cause, aliases, violations, file_path);
                }
            }
            Stmt::FunctionDef(function) => {
                for decorator in &function.decorator_list {
                    Self::check_expr(decorator, aliases, violations, file_path);
                }
                if let Some(returns) = &function.returns {
                    Self::check_expr(returns, aliases, violations, file_path);
                }
            }
            Stmt::AsyncFunctionDef(function) => {
                for decorator in &function.decorator_list {
                    Self::check_expr(decorator, aliases, violations, file_path);
                }
                if let Some(returns) = &function.returns {
                    Self::check_expr(returns, aliases, violations, file_path);
                }
            }
            Stmt::ClassDef(class_def) => {
                for decorator in &class_def.decorator_list {
                    Self::check_expr(decorator, aliases, violations, file_path);
                }
                for base in &class_def.bases {
                    Self::check_expr(base, aliases, violations, file_path);
                }
                for keyword in &class_def.keywords {
                    Self::check_expr(&keyword.value, aliases, violations, file_path);
                }
            }
            _ => {}
        }
    }

    fn create_violation(
        construct: String,
        replacement: Replacement,
        offset: usize,
        file_path: &str,
    ) -> Violation {
        let message = format!(
            "Workflow glue code must be deterministic, but this module uses {construct}.\n\n\
             Problem: raw nondeterminism breaks workflow replay and durable resume.\n\n\
             Replacement: {} ({}).",
            replacement.suggestion(),
            replacement.token()
        );

        Violation::new(
            RULE_ID.to_string(),
            message,
            offset,
            file_path.to_string(),
            Severity::Error,
        )
    }
}

impl LintRule for WorkflowNondeterminismRule {
    fn rule_id(&self) -> &str {
        RULE_ID
    }

    fn description(&self) -> &str {
        "Ban raw nondeterminism in workflow modules"
    }

    fn check(&self, context: &RuleContext) -> Vec<Violation> {
        let mut violations = Vec::new();

        if !Self::is_workflow_module(context.file_path, context.source) {
            return violations;
        }

        Self::check_import(context.stmt, &mut violations, context.file_path);

        let aliases = Self::collect_import_aliases(context.ast);
        Self::check_stmt_exprs(context.stmt, &aliases, &mut violations, context.file_path);

        violations
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rustpython_ast::Mod;
    use rustpython_parser::{parse, Mode};

    fn check_code_with_path(code: &str, file_path: &str) -> Vec<Violation> {
        let ast = parse(code, Mode::Module, file_path).unwrap();
        let rule = WorkflowNondeterminismRule::new();
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

    fn check_code(code: &str) -> Vec<Violation> {
        check_code_with_path(code, "workflow.py")
    }

    #[test]
    fn test_requires_workflow_marker_or_path() {
        let violations =
            check_code_with_path("import random\nvalue = random.random()", "module.py");
        assert!(violations.is_empty());
    }

    #[test]
    fn test_datetime_now_suggests_time_effect() {
        let violations =
            check_code("# doeff: workflow\nfrom datetime import datetime\nnow = datetime.now()");
        assert!(violations.iter().any(|v| v.message.contains("time!")));
    }

    #[test]
    fn test_random_suggests_random_effect() {
        let violations = check_code("# doeff: workflow\nimport random\nvalue = random.choice([1])");
        assert!(violations.iter().any(|v| v.message.contains("random!")));
    }

    #[test]
    fn test_gate_suggested_for_open() {
        let violations = check_code("# doeff: workflow\nvalue = open('x').read()");
        assert!(violations.iter().any(|v| v.message.contains("gate!")));
    }

    #[test]
    fn test_params_suggested_for_non_allowlisted_import() {
        let violations =
            check_code("# doeff: workflow\nimport yaml\nvalue = yaml.safe_load('x: 1')");
        assert!(violations.iter().any(|v| v.message.contains(":params")));
    }

    #[test]
    fn test_clean_workflow_imports_allowed() {
        let violations = check_code(
            "# doeff: workflow\nfrom dataclasses import dataclass\nfrom typing import Mapping\n",
        );
        assert!(violations.is_empty());
    }
}

//! DOEFF012: No Append Loop Pattern
//!
//! Detects the anti-pattern of initializing an empty list followed by a for loop
//! that appends to it. Suggests using list comprehensions or extracting the
//! processing logic into a named function.
//!
//! Bad:
//! ```python
//! data = []
//! for item in items:
//!     # ... long processing ...
//!     data.append(process(item))
//! ```
//!
//! Good:
//! ```python
//! def process_item(item):
//!     # ... processing logic ...
//!     return processed
//!
//! data = [process_item(item) for item in items]
//! ```
//!
//! Allowed (visualization context):
//! ```python
//! import matplotlib.pyplot as plt
//!
//! x_values = []
//! y_values = []
//! for point in data:
//!     x_values.append(point.x)
//!     y_values.append(point.y)
//! plt.plot(x_values, y_values)
//! ```

use crate::models::{RuleContext, Severity, Violation};
use crate::rules::base::LintRule;
use rustpython_ast::{Expr, Mod, Stmt};
use std::collections::HashSet;

/// Known visualization library modules that are allowed to use append loops
const VISUALIZATION_MODULES: &[&str] = &[
    "matplotlib",
    "matplotlib.pyplot",
    "seaborn",
    "plotly",
    "plotly.express",
    "plotly.graph_objects",
    "bokeh",
    "altair",
    "pygal",
    "vispy",
    "mayavi",
];

pub struct NoAppendLoopRule;

impl NoAppendLoopRule {
    pub fn new() -> Self {
        Self
    }

    /// Check if an expression is an empty list literal `[]`
    fn is_empty_list(expr: &Expr) -> bool {
        match expr {
            Expr::List(list) => list.elts.is_empty(),
            _ => false,
        }
    }

    /// Extract all visualization-related import aliases from the module
    /// Returns a set of aliases that refer to visualization libraries
    fn collect_visualization_aliases(stmts: &[Stmt]) -> HashSet<String> {
        let mut aliases = HashSet::new();

        for stmt in stmts {
            match stmt {
                // import matplotlib.pyplot as plt
                Stmt::Import(import_stmt) => {
                    for alias in &import_stmt.names {
                        let module_name = alias.name.as_str();
                        if Self::is_visualization_module(module_name) {
                            // Use alias if present, otherwise use the module name
                            let name = alias
                                .asname
                                .as_ref()
                                .map(|s| s.as_str())
                                .unwrap_or(module_name);
                            aliases.insert(name.to_string());
                            // Also add the first part of the module name for qualified access
                            if let Some(first_part) = module_name.split('.').next() {
                                aliases.insert(first_part.to_string());
                            }
                        }
                    }
                }
                // from matplotlib import pyplot as plt
                // from matplotlib.pyplot import plot
                Stmt::ImportFrom(import_from) => {
                    if let Some(module) = &import_from.module {
                        let module_name = module.as_str();
                        if Self::is_visualization_module(module_name) {
                            for alias in &import_from.names {
                                let imported_name = alias.name.as_str();
                                let name = alias
                                    .asname
                                    .as_ref()
                                    .map(|s| s.as_str())
                                    .unwrap_or(imported_name);
                                aliases.insert(name.to_string());
                            }
                        }
                    }
                }
                _ => {}
            }
        }

        aliases
    }

    /// Check if a module name is a visualization library
    fn is_visualization_module(module_name: &str) -> bool {
        VISUALIZATION_MODULES
            .iter()
            .any(|&viz_mod| module_name == viz_mod || module_name.starts_with(&format!("{}.", viz_mod)))
    }

    /// Check if a variable is used in any visualization function call within the given statements
    fn is_used_in_visualization_context(
        stmts: &[Stmt],
        var_name: &str,
        viz_aliases: &HashSet<String>,
    ) -> bool {
        for stmt in stmts {
            if Self::stmt_uses_var_in_viz_call(stmt, var_name, viz_aliases) {
                return true;
            }
        }
        false
    }

    /// Check if a statement uses the variable in a visualization call
    fn stmt_uses_var_in_viz_call(stmt: &Stmt, var_name: &str, viz_aliases: &HashSet<String>) -> bool {
        match stmt {
            Stmt::Expr(expr_stmt) => {
                Self::expr_uses_var_in_viz_call(&expr_stmt.value, var_name, viz_aliases)
            }
            Stmt::Assign(assign) => {
                Self::expr_uses_var_in_viz_call(&assign.value, var_name, viz_aliases)
            }
            Stmt::AnnAssign(ann_assign) => {
                if let Some(value) = &ann_assign.value {
                    Self::expr_uses_var_in_viz_call(value, var_name, viz_aliases)
                } else {
                    false
                }
            }
            Stmt::Return(ret) => {
                if let Some(value) = &ret.value {
                    Self::expr_uses_var_in_viz_call(value, var_name, viz_aliases)
                } else {
                    false
                }
            }
            Stmt::If(if_stmt) => {
                Self::is_used_in_visualization_context(&if_stmt.body, var_name, viz_aliases)
                    || Self::is_used_in_visualization_context(&if_stmt.orelse, var_name, viz_aliases)
            }
            Stmt::For(for_stmt) => {
                Self::is_used_in_visualization_context(&for_stmt.body, var_name, viz_aliases)
            }
            Stmt::While(while_stmt) => {
                Self::is_used_in_visualization_context(&while_stmt.body, var_name, viz_aliases)
            }
            Stmt::With(with_stmt) => {
                Self::is_used_in_visualization_context(&with_stmt.body, var_name, viz_aliases)
            }
            Stmt::Try(try_stmt) => {
                Self::is_used_in_visualization_context(&try_stmt.body, var_name, viz_aliases)
                    || Self::is_used_in_visualization_context(&try_stmt.orelse, var_name, viz_aliases)
                    || Self::is_used_in_visualization_context(&try_stmt.finalbody, var_name, viz_aliases)
            }
            Stmt::FunctionDef(func) => {
                Self::is_used_in_visualization_context(&func.body, var_name, viz_aliases)
            }
            Stmt::AsyncFunctionDef(func) => {
                Self::is_used_in_visualization_context(&func.body, var_name, viz_aliases)
            }
            _ => false,
        }
    }

    /// Check if an expression is a visualization function call that uses the variable
    fn expr_uses_var_in_viz_call(expr: &Expr, var_name: &str, viz_aliases: &HashSet<String>) -> bool {
        match expr {
            Expr::Call(call) => {
                // Check if this is a visualization call (e.g., plt.plot, ax.scatter)
                let is_viz_call = Self::is_visualization_call(&call.func, viz_aliases);

                // Check if var_name is used in the arguments
                let var_used_in_args = call.args.iter().any(|arg| Self::expr_contains_var(arg, var_name))
                    || call.keywords.iter().any(|kw| Self::expr_contains_var(&kw.value, var_name));

                if is_viz_call && var_used_in_args {
                    return true;
                }

                // Recursively check call arguments and func
                Self::expr_uses_var_in_viz_call(&call.func, var_name, viz_aliases)
                    || call.args.iter().any(|arg| Self::expr_uses_var_in_viz_call(arg, var_name, viz_aliases))
                    || call.keywords.iter().any(|kw| Self::expr_uses_var_in_viz_call(&kw.value, var_name, viz_aliases))
            }
            Expr::List(list) => list.elts.iter().any(|e| Self::expr_uses_var_in_viz_call(e, var_name, viz_aliases)),
            Expr::Tuple(tuple) => tuple.elts.iter().any(|e| Self::expr_uses_var_in_viz_call(e, var_name, viz_aliases)),
            Expr::Dict(dict) => {
                dict.keys.iter().filter_map(|k| k.as_ref()).any(|k| Self::expr_uses_var_in_viz_call(k, var_name, viz_aliases))
                    || dict.values.iter().any(|v| Self::expr_uses_var_in_viz_call(v, var_name, viz_aliases))
            }
            Expr::BinOp(binop) => {
                Self::expr_uses_var_in_viz_call(&binop.left, var_name, viz_aliases)
                    || Self::expr_uses_var_in_viz_call(&binop.right, var_name, viz_aliases)
            }
            Expr::UnaryOp(unaryop) => Self::expr_uses_var_in_viz_call(&unaryop.operand, var_name, viz_aliases),
            Expr::IfExp(ifexp) => {
                Self::expr_uses_var_in_viz_call(&ifexp.test, var_name, viz_aliases)
                    || Self::expr_uses_var_in_viz_call(&ifexp.body, var_name, viz_aliases)
                    || Self::expr_uses_var_in_viz_call(&ifexp.orelse, var_name, viz_aliases)
            }
            Expr::Subscript(sub) => {
                Self::expr_uses_var_in_viz_call(&sub.value, var_name, viz_aliases)
                    || Self::expr_uses_var_in_viz_call(&sub.slice, var_name, viz_aliases)
            }
            Expr::Attribute(attr) => Self::expr_uses_var_in_viz_call(&attr.value, var_name, viz_aliases),
            _ => false,
        }
    }

    /// Check if an expression contains a reference to the variable
    fn expr_contains_var(expr: &Expr, var_name: &str) -> bool {
        match expr {
            Expr::Name(name) => name.id.as_str() == var_name,
            Expr::Call(call) => {
                Self::expr_contains_var(&call.func, var_name)
                    || call.args.iter().any(|arg| Self::expr_contains_var(arg, var_name))
                    || call.keywords.iter().any(|kw| Self::expr_contains_var(&kw.value, var_name))
            }
            Expr::List(list) => list.elts.iter().any(|e| Self::expr_contains_var(e, var_name)),
            Expr::Tuple(tuple) => tuple.elts.iter().any(|e| Self::expr_contains_var(e, var_name)),
            Expr::Dict(dict) => {
                dict.keys.iter().filter_map(|k| k.as_ref()).any(|k| Self::expr_contains_var(k, var_name))
                    || dict.values.iter().any(|v| Self::expr_contains_var(v, var_name))
            }
            Expr::BinOp(binop) => {
                Self::expr_contains_var(&binop.left, var_name)
                    || Self::expr_contains_var(&binop.right, var_name)
            }
            Expr::UnaryOp(unaryop) => Self::expr_contains_var(&unaryop.operand, var_name),
            Expr::Compare(cmp) => {
                Self::expr_contains_var(&cmp.left, var_name)
                    || cmp.comparators.iter().any(|c| Self::expr_contains_var(c, var_name))
            }
            Expr::IfExp(ifexp) => {
                Self::expr_contains_var(&ifexp.test, var_name)
                    || Self::expr_contains_var(&ifexp.body, var_name)
                    || Self::expr_contains_var(&ifexp.orelse, var_name)
            }
            Expr::Subscript(sub) => {
                Self::expr_contains_var(&sub.value, var_name)
                    || Self::expr_contains_var(&sub.slice, var_name)
            }
            Expr::Slice(slice) => {
                slice.lower.as_ref().map_or(false, |e| Self::expr_contains_var(e, var_name))
                    || slice.upper.as_ref().map_or(false, |e| Self::expr_contains_var(e, var_name))
                    || slice.step.as_ref().map_or(false, |e| Self::expr_contains_var(e, var_name))
            }
            Expr::Attribute(attr) => Self::expr_contains_var(&attr.value, var_name),
            Expr::Starred(starred) => Self::expr_contains_var(&starred.value, var_name),
            Expr::ListComp(lc) => Self::expr_contains_var(&lc.elt, var_name),
            Expr::SetComp(sc) => Self::expr_contains_var(&sc.elt, var_name),
            Expr::GeneratorExp(ge) => Self::expr_contains_var(&ge.elt, var_name),
            Expr::DictComp(dc) => {
                Self::expr_contains_var(&dc.key, var_name)
                    || Self::expr_contains_var(&dc.value, var_name)
            }
            Expr::Await(aw) => Self::expr_contains_var(&aw.value, var_name),
            Expr::Yield(y) => y.value.as_ref().map_or(false, |v| Self::expr_contains_var(v, var_name)),
            Expr::YieldFrom(yf) => Self::expr_contains_var(&yf.value, var_name),
            Expr::FormattedValue(fv) => Self::expr_contains_var(&fv.value, var_name),
            Expr::JoinedStr(js) => js.values.iter().any(|v| Self::expr_contains_var(v, var_name)),
            _ => false,
        }
    }

    /// Check if a function call expression is to a visualization library
    fn is_visualization_call(func: &Expr, viz_aliases: &HashSet<String>) -> bool {
        match func {
            // plt.plot(...), ax.scatter(...)
            Expr::Attribute(attr) => {
                if let Expr::Name(name) = &*attr.value {
                    viz_aliases.contains(name.id.as_str())
                } else {
                    // Check for chained attribute access like fig.gca().plot(...)
                    Self::is_visualization_call(&attr.value, viz_aliases)
                }
            }
            // Direct function call like plot(...) after `from matplotlib.pyplot import plot`
            Expr::Name(name) => viz_aliases.contains(name.id.as_str()),
            _ => false,
        }
    }

    /// Extract the variable name from an assignment target if it's a simple name
    fn get_assign_name(target: &Expr) -> Option<&str> {
        match target {
            Expr::Name(name) => Some(name.id.as_str()),
            _ => None,
        }
    }

    /// Check if a statement is an append call to the specified variable
    fn is_append_to_var(stmt: &Stmt, var_name: &str) -> bool {
        match stmt {
            Stmt::Expr(expr_stmt) => {
                if let Expr::Call(call) = &*expr_stmt.value {
                    if let Expr::Attribute(attr) = &*call.func {
                        if attr.attr.as_str() == "append" {
                            if let Expr::Name(name) = &*attr.value {
                                return name.id.as_str() == var_name;
                            }
                        }
                    }
                }
                false
            }
            _ => false,
        }
    }

    /// Check if a for loop body contains any append call to the variable
    fn for_loop_appends_to(for_stmt: &rustpython_ast::StmtFor, var_name: &str) -> bool {
        Self::body_contains_append(&for_stmt.body, var_name)
    }

    /// Recursively check if a body contains an append to the variable
    fn body_contains_append(body: &[Stmt], var_name: &str) -> bool {
        for stmt in body {
            if Self::is_append_to_var(stmt, var_name) {
                return true;
            }
            // Check nested structures
            match stmt {
                Stmt::If(if_stmt) => {
                    if Self::body_contains_append(&if_stmt.body, var_name)
                        || Self::body_contains_append(&if_stmt.orelse, var_name)
                    {
                        return true;
                    }
                }
                Stmt::For(nested_for) => {
                    if Self::body_contains_append(&nested_for.body, var_name) {
                        return true;
                    }
                }
                Stmt::While(while_stmt) => {
                    if Self::body_contains_append(&while_stmt.body, var_name) {
                        return true;
                    }
                }
                Stmt::With(with_stmt) => {
                    if Self::body_contains_append(&with_stmt.body, var_name) {
                        return true;
                    }
                }
                Stmt::Try(try_stmt) => {
                    if Self::body_contains_append(&try_stmt.body, var_name) {
                        return true;
                    }
                    for handler in &try_stmt.handlers {
                        if let rustpython_ast::ExceptHandler::ExceptHandler(h) = handler {
                            if Self::body_contains_append(&h.body, var_name) {
                                return true;
                            }
                        }
                    }
                }
                _ => {}
            }
        }
        false
    }

    /// Check if an expression contains a yield
    fn expr_contains_yield(expr: &Expr) -> bool {
        match expr {
            Expr::Yield(_) | Expr::YieldFrom(_) => true,
            Expr::Call(call) => {
                Self::expr_contains_yield(&call.func)
                    || call.args.iter().any(|arg| Self::expr_contains_yield(arg))
                    || call.keywords.iter().any(|kw| Self::expr_contains_yield(&kw.value))
            }
            Expr::List(list) => list.elts.iter().any(|e| Self::expr_contains_yield(e)),
            Expr::Tuple(tuple) => tuple.elts.iter().any(|e| Self::expr_contains_yield(e)),
            Expr::Dict(dict) => {
                dict.keys.iter().filter_map(|k| k.as_ref()).any(|k| Self::expr_contains_yield(k))
                    || dict.values.iter().any(|v| Self::expr_contains_yield(v))
            }
            Expr::BinOp(binop) => {
                Self::expr_contains_yield(&binop.left) || Self::expr_contains_yield(&binop.right)
            }
            Expr::UnaryOp(unaryop) => Self::expr_contains_yield(&unaryop.operand),
            Expr::Compare(cmp) => {
                Self::expr_contains_yield(&cmp.left)
                    || cmp.comparators.iter().any(|c| Self::expr_contains_yield(c))
            }
            Expr::IfExp(ifexp) => {
                Self::expr_contains_yield(&ifexp.test)
                    || Self::expr_contains_yield(&ifexp.body)
                    || Self::expr_contains_yield(&ifexp.orelse)
            }
            Expr::Subscript(sub) => {
                Self::expr_contains_yield(&sub.value) || Self::expr_contains_yield(&sub.slice)
            }
            Expr::Attribute(attr) => Self::expr_contains_yield(&attr.value),
            Expr::Starred(starred) => Self::expr_contains_yield(&starred.value),
            Expr::Await(aw) => Self::expr_contains_yield(&aw.value),
            Expr::FormattedValue(fv) => Self::expr_contains_yield(&fv.value),
            Expr::JoinedStr(js) => js.values.iter().any(|v| Self::expr_contains_yield(v)),
            _ => false,
        }
    }

    /// Recursively check if a body contains yield expressions (indicating @do function context)
    fn body_contains_yield(body: &[Stmt]) -> bool {
        for stmt in body {
            match stmt {
                Stmt::Expr(expr_stmt) => {
                    if Self::expr_contains_yield(&expr_stmt.value) {
                        return true;
                    }
                }
                Stmt::Assign(assign) => {
                    if Self::expr_contains_yield(&assign.value) {
                        return true;
                    }
                }
                Stmt::AnnAssign(ann_assign) => {
                    if let Some(value) = &ann_assign.value {
                        if Self::expr_contains_yield(value) {
                            return true;
                        }
                    }
                }
                Stmt::If(if_stmt) => {
                    if Self::expr_contains_yield(&if_stmt.test)
                        || Self::body_contains_yield(&if_stmt.body)
                        || Self::body_contains_yield(&if_stmt.orelse)
                    {
                        return true;
                    }
                }
                Stmt::For(for_stmt) => {
                    if Self::body_contains_yield(&for_stmt.body) {
                        return true;
                    }
                }
                Stmt::While(while_stmt) => {
                    if Self::expr_contains_yield(&while_stmt.test)
                        || Self::body_contains_yield(&while_stmt.body)
                    {
                        return true;
                    }
                }
                Stmt::With(with_stmt) => {
                    if Self::body_contains_yield(&with_stmt.body) {
                        return true;
                    }
                }
                Stmt::Try(try_stmt) => {
                    if Self::body_contains_yield(&try_stmt.body)
                        || Self::body_contains_yield(&try_stmt.orelse)
                        || Self::body_contains_yield(&try_stmt.finalbody)
                    {
                        return true;
                    }
                    for handler in &try_stmt.handlers {
                        if let rustpython_ast::ExceptHandler::ExceptHandler(h) = handler {
                            if Self::body_contains_yield(&h.body) {
                                return true;
                            }
                        }
                    }
                }
                _ => {}
            }
        }
        false
    }

    /// Count the number of statements in a for loop body (for detecting "long" loops)
    fn count_body_statements(body: &[Stmt]) -> usize {
        let mut count = 0;
        for stmt in body {
            count += 1;
            match stmt {
                Stmt::If(if_stmt) => {
                    count += Self::count_body_statements(&if_stmt.body);
                    count += Self::count_body_statements(&if_stmt.orelse);
                }
                Stmt::For(for_stmt) => {
                    count += Self::count_body_statements(&for_stmt.body);
                }
                Stmt::While(while_stmt) => {
                    count += Self::count_body_statements(&while_stmt.body);
                }
                Stmt::With(with_stmt) => {
                    count += Self::count_body_statements(&with_stmt.body);
                }
                Stmt::Try(try_stmt) => {
                    count += Self::count_body_statements(&try_stmt.body);
                    for handler in &try_stmt.handlers {
                        if let rustpython_ast::ExceptHandler::ExceptHandler(h) = handler {
                            count += Self::count_body_statements(&h.body);
                        }
                    }
                }
                _ => {}
            }
        }
        count
    }

    /// Analyze a sequence of statements for the append loop pattern
    fn check_statement_sequence(
        stmts: &[Stmt],
        file_path: &str,
        viz_aliases: &HashSet<String>,
    ) -> Vec<Violation> {
        let mut violations = Vec::new();
        let mut empty_list_vars: HashSet<(String, usize)> = HashSet::new();

        for (idx, stmt) in stmts.iter().enumerate() {
            // Check for empty list assignment: `data = []`
            if let Stmt::Assign(assign) = stmt {
                if Self::is_empty_list(&assign.value) {
                    for target in &assign.targets {
                        if let Some(name) = Self::get_assign_name(target) {
                            empty_list_vars.insert((name.to_string(), idx));
                        }
                    }
                }
            }

            // Check for annotated assignment: `data: list[T] = []`
            if let Stmt::AnnAssign(ann_assign) = stmt {
                if let Some(value) = &ann_assign.value {
                    if Self::is_empty_list(value) {
                        if let Some(name) = Self::get_assign_name(&ann_assign.target) {
                            empty_list_vars.insert((name.to_string(), idx));
                        }
                    }
                }
            }

            // Check for for loop that appends to a previously empty-initialized variable
            if let Stmt::For(for_stmt) = stmt {
                for (var_name, assign_idx) in &empty_list_vars {
                    // Only check if the for loop comes after the assignment
                    if idx > *assign_idx && Self::for_loop_appends_to(for_stmt, var_name) {
                        // Skip if the variable is used in a visualization context
                        if !viz_aliases.is_empty()
                            && Self::is_used_in_visualization_context(stmts, var_name, viz_aliases)
                        {
                            continue;
                        }

                        let body_size = Self::count_body_statements(&for_stmt.body);
                        let has_yield = Self::body_contains_yield(&for_stmt.body);

                        let message = if has_yield {
                            // @do function context with yield - suggest gather pattern
                            format!(
                                "Refactor append loop for '{}' into pipeline style using gather. \
                                 Extract the loop body into a separate @do function:\n\n\
                                 \x20   @do\n\
                                 \x20   def process_item(item):\n\
                                 \x20       data = yield do_something(item)\n\
                                 \x20       yield slog(...)\n\
                                 \x20       return data\n\n\
                                 \x20   {} = yield gather(*[process_item(x) for x in items])\n\n\
                                 This eliminates mutable state and enables parallel execution. \
                                 Use `# noqa: DOEFF012` only as last resort for true stateful accumulation.",
                                var_name, var_name
                            )
                        } else if body_size > 3 {
                            // Complex loop without yield - suggest extracting function
                            format!(
                                "Refactor append loop for '{}' ({} statements) into pipeline style. \
                                 Extract the loop body into a focused function:\n\n\
                                 \x20   def process_item(x):\n\
                                 \x20       # ... transformation logic ...\n\
                                 \x20       return result\n\n\
                                 \x20   {} = [process_item(x) for x in items]\n\
                                 \x20   # or: {} = list(map(process_item, items))\n\n\
                                 Benefits: composable, testable, reusable transformations. \
                                 Use `# noqa: DOEFF012` only as last resort for true mutation patterns.",
                                var_name, body_size, var_name, var_name
                            )
                        } else {
                            // Simple loop without yield
                            format!(
                                "Prefer pipeline style over append loop for '{}'. \
                                 Extract the transformation into a local function:\n\n\
                                 \x20   def process_item(x):\n\
                                 \x20       ...\n\
                                 \x20       return result\n\n\
                                 \x20   {} = [process_item(x) for x in items]\n\n\
                                 Use `# noqa: DOEFF012` only as last resort for true mutation patterns.",
                                var_name, var_name
                            )
                        };

                        violations.push(Violation::new(
                            "DOEFF012".to_string(),
                            message,
                            for_stmt.range.start().to_usize(),
                            file_path.to_string(),
                            Severity::Warning,
                        ));
                    }
                }
            }
        }

        // Recursively check nested scopes
        for stmt in stmts {
            match stmt {
                Stmt::FunctionDef(func) => {
                    violations.extend(Self::check_statement_sequence(&func.body, file_path, viz_aliases));
                }
                Stmt::AsyncFunctionDef(func) => {
                    violations.extend(Self::check_statement_sequence(&func.body, file_path, viz_aliases));
                }
                Stmt::ClassDef(class_def) => {
                    violations.extend(Self::check_statement_sequence(&class_def.body, file_path, viz_aliases));
                }
                Stmt::If(if_stmt) => {
                    violations.extend(Self::check_statement_sequence(&if_stmt.body, file_path, viz_aliases));
                    violations.extend(Self::check_statement_sequence(&if_stmt.orelse, file_path, viz_aliases));
                }
                Stmt::For(for_stmt) => {
                    violations.extend(Self::check_statement_sequence(&for_stmt.body, file_path, viz_aliases));
                }
                Stmt::While(while_stmt) => {
                    violations.extend(Self::check_statement_sequence(&while_stmt.body, file_path, viz_aliases));
                }
                Stmt::With(with_stmt) => {
                    violations.extend(Self::check_statement_sequence(&with_stmt.body, file_path, viz_aliases));
                }
                Stmt::Try(try_stmt) => {
                    violations.extend(Self::check_statement_sequence(&try_stmt.body, file_path, viz_aliases));
                    for handler in &try_stmt.handlers {
                        if let rustpython_ast::ExceptHandler::ExceptHandler(h) = handler {
                            violations.extend(Self::check_statement_sequence(&h.body, file_path, viz_aliases));
                        }
                    }
                    violations.extend(Self::check_statement_sequence(&try_stmt.orelse, file_path, viz_aliases));
                    violations.extend(Self::check_statement_sequence(&try_stmt.finalbody, file_path, viz_aliases));
                }
                _ => {}
            }
        }

        violations
    }
}

impl LintRule for NoAppendLoopRule {
    fn rule_id(&self) -> &str {
        "DOEFF012"
    }

    fn description(&self) -> &str {
        "Prefer list comprehensions or named functions over append loops"
    }

    fn check(&self, context: &RuleContext) -> Vec<Violation> {
        // Only run once per file (when we see the first statement)
        if let Mod::Module(module) = context.ast {
            // Check if this is the first statement to avoid duplicate checks
            if let Some(first_stmt) = module.body.first() {
                if std::ptr::eq(context.stmt, first_stmt) {
                    // Collect visualization library aliases from imports
                    let viz_aliases = Self::collect_visualization_aliases(&module.body);
                    return Self::check_statement_sequence(&module.body, context.file_path, &viz_aliases);
                }
            }
        }
        vec![]
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rustpython_parser::{parse, Mode};

    fn check_code(code: &str) -> Vec<Violation> {
        let ast = parse(code, Mode::Module, "test.py").unwrap();
        let rule = NoAppendLoopRule::new();

        if let Mod::Module(module) = &ast {
            if let Some(first_stmt) = module.body.first() {
                let context = RuleContext {
                    stmt: first_stmt,
                    file_path: "test.py",
                    source: code,
                    ast: &ast,
                };
                return rule.check(&context);
            }
        }
        vec![]
    }

    #[test]
    fn test_basic_append_loop() {
        let code = r#"
data = []
for item in items:
    data.append(process(item))
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("data"));
        assert!(violations[0].message.contains("pipeline style"));
        assert!(violations[0].message.contains("process_item"));
        assert!(violations[0].message.contains("last resort"));
    }

    #[test]
    fn test_long_append_loop() {
        let code = r#"
results = []
for item in processing_target:
    step1 = do_something(item)
    step2 = transform(step1)
    validated = validate(step2)
    if validated:
        final = finalize(validated)
        results.append(final)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("Extract"));
        assert!(violations[0].message.contains("focused function"));
        assert!(violations[0].message.contains("composable, testable, reusable"));
    }

    #[test]
    fn test_yield_context_suggests_gather() {
        let code = r#"
results = []
for item in items:
    data = yield do_something(item)
    yield slog(data)
    results.append(data)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("gather"));
        assert!(violations[0].message.contains("@do"));
        assert!(violations[0].message.contains("eliminates mutable state"));
        assert!(violations[0].message.contains("parallel execution"));
    }

    #[test]
    fn test_no_violation_for_comprehension() {
        let code = r#"
data = [process(item) for item in items]
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_no_violation_for_non_empty_init() {
        let code = r#"
data = [1, 2, 3]
for item in items:
    data.append(process(item))
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_annotated_empty_list() {
        let code = r#"
data: list[int] = []
for item in items:
    data.append(int(item))
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_nested_append() {
        let code = r#"
data = []
for item in items:
    if condition:
        data.append(item)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_inside_function() {
        let code = r#"
def process_items(items):
    results = []
    for item in items:
        results.append(transform(item))
    return results
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_chained_pipelines() {
        let code = r#"
data = []
for item in processing_target:
    data.append(process(item))

second_stage_data = []
for item in data:
    second_stage_data.append(process2(item))
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 2);
    }

    #[test]
    fn test_different_variable_no_violation() {
        let code = r#"
data = []
other = []
for item in items:
    other.append(item)
"#;
        // data is empty but we append to other, so no violation for data
        // other was also initialized empty, so there should be 1 violation
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("other"));
    }

    #[test]
    fn test_visualization_context_matplotlib_import() {
        let code = r#"
import matplotlib.pyplot as plt

x_values = []
y_values = []
for point in data:
    x_values.append(point.x)
    y_values.append(point.y)
plt.plot(x_values, y_values)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0, "Should allow append loops used in visualization");
    }

    #[test]
    fn test_visualization_context_from_import() {
        let code = r#"
from matplotlib import pyplot as plt

values = []
for v in data:
    values.append(v)
plt.scatter(range(len(values)), values)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0, "Should allow append loops used in visualization");
    }

    #[test]
    fn test_visualization_context_seaborn() {
        let code = r#"
import seaborn as sns

x = []
y = []
for point in points:
    x.append(point[0])
    y.append(point[1])
sns.lineplot(x=x, y=y)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0, "Should allow append loops used in seaborn");
    }

    #[test]
    fn test_visualization_context_plotly() {
        let code = r#"
import plotly.express as px

values = []
for item in items:
    values.append(item.value)
px.bar(x=range(len(values)), y=values)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0, "Should allow append loops used in plotly");
    }

    #[test]
    fn test_visualization_not_used_still_violates() {
        // Even with matplotlib imported, if the list is not used in visualization, it should violate
        let code = r#"
import matplotlib.pyplot as plt

data = []
for item in items:
    data.append(process(item))

# Note: data is not used in any plt call
result = some_function(data)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1, "Should still violate if list is not used in visualization");
    }

    #[test]
    fn test_no_visualization_import_still_violates() {
        let code = r#"
x_values = []
y_values = []
for point in data:
    x_values.append(point.x)
    y_values.append(point.y)
plot(x_values, y_values)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 2, "Should violate without visualization imports");
    }

    #[test]
    fn test_visualization_inside_function() {
        let code = r#"
import matplotlib.pyplot as plt

def create_plot(data):
    x = []
    y = []
    for point in data:
        x.append(point[0])
        y.append(point[1])
    plt.plot(x, y)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0, "Should allow append loops in functions with visualization");
    }
}


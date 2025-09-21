use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use serde::Serialize;
use walkdir::WalkDir;

use rustpython_ast::{self as ast, Constant, Expr, Mod, Stmt};
use rustpython_parser::{parse, Mode};

use crate::indexer::{
    compute_module_path, expr_to_string, is_do_decorator, is_python_file, should_descend, LineIndex,
};

const DEFAULT_DO_WRAPPERS: &[&str] = &["cache", "doeff.cache.cache"];

#[derive(Debug, Clone)]
struct DoFunctionStub {
    qualified_name: String,
    simple_name: String,
    file_path: PathBuf,
    line: usize,
}

#[derive(Debug, Clone)]
struct DoAlias {
    qualified_name: String,
    simple_name: String,
    module_path: String,
    target_expr: String,
    target_simple: Option<String>,
    file_path: PathBuf,
    line: usize,
}

#[derive(Debug, Clone)]
struct GlobalDoMap {
    by_qualified: BTreeMap<String, DoFunctionStub>,
    by_simple: HashMap<String, Vec<String>>,
    alias_by_qualified: HashMap<String, String>,
    alias_simple_resolved: HashMap<String, String>,
    alias_unresolved_by_simple: HashMap<String, String>,
    alias_unresolved_by_qualified: HashMap<String, String>,
}

impl GlobalDoMap {
    fn new(stubs: &[DoFunctionStub], aliases: &[DoAlias]) -> Self {
        let mut by_qualified = BTreeMap::new();
        let mut by_simple: HashMap<String, Vec<String>> = HashMap::new();

        for stub in stubs {
            by_qualified.insert(stub.qualified_name.clone(), stub.clone());
            by_simple
                .entry(stub.simple_name.clone())
                .or_default()
                .push(stub.qualified_name.clone());
        }

        let mut alias_by_qualified = HashMap::new();
        let mut alias_simple_resolved = HashMap::new();
        let mut alias_unresolved_by_simple = HashMap::new();
        let mut alias_unresolved_by_qualified = HashMap::new();

        for alias in aliases {
            // Try to resolve target via fully qualified name string
            let resolved = if by_qualified.contains_key(&alias.target_expr) {
                Some(alias.target_expr.clone())
            } else if !alias.module_path.is_empty() {
                let candidate = format!("{}.{}", alias.module_path, alias.target_expr);
                by_qualified
                    .get(&candidate)
                    .map(|stub| stub.qualified_name.clone())
            } else if let Some(target_simple) = alias.target_simple.as_deref() {
                match by_simple.get(target_simple) {
                    Some(candidates) if candidates.len() == 1 => Some(candidates[0].clone()),
                    _ => None,
                }
            } else {
                None
            };

            match resolved {
                Some(target) => {
                    alias_by_qualified.insert(alias.qualified_name.clone(), target.clone());
                    alias_simple_resolved.insert(alias.simple_name.clone(), target.clone());
                }
                None => {
                    alias_unresolved_by_simple
                        .insert(alias.simple_name.clone(), alias.target_expr.clone());
                    alias_unresolved_by_qualified
                        .insert(alias.qualified_name.clone(), alias.target_expr.clone());
                }
            }
        }

        Self {
            by_qualified,
            by_simple,
            alias_by_qualified,
            alias_simple_resolved,
            alias_unresolved_by_simple,
            alias_unresolved_by_qualified,
        }
    }

    fn candidates_for_simple(&self, name: &str) -> Option<&Vec<String>> {
        self.by_simple.get(name)
    }

    fn alias_target_for_simple(&self, name: &str) -> Option<&String> {
        self.alias_simple_resolved.get(name)
    }

    fn alias_target_for_qualified(&self, qualified: &str) -> Option<&String> {
        self.alias_by_qualified.get(qualified)
    }

    fn unresolved_alias_simple(&self, name: &str) -> Option<&String> {
        self.alias_unresolved_by_simple.get(name)
    }

    fn unresolved_alias_qualified(&self, qualified: &str) -> Option<&String> {
        self.alias_unresolved_by_qualified.get(qualified)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum EffectKind {
    Dep,
    Ask,
}

#[derive(Debug, Clone, Serialize)]
pub struct FunctionDependency {
    pub qualified_name: String,
    pub file_path: String,
    pub line: usize,
    pub direct_dep_keys: Vec<String>,
    pub all_dep_keys: Vec<String>,
    pub direct_ask_keys: Vec<String>,
    pub all_ask_keys: Vec<String>,
    pub direct_calls: Vec<String>,
    pub unresolved_calls: Vec<String>,
}

#[derive(Clone)]
enum AliasTarget {
    DoFunction(String),
    Effect(EffectKind, String),
}

enum CalleeResolution {
    Resolved(String),
    Unresolved(String),
}

struct FunctionContext<'a> {
    global: &'a GlobalDoMap,
    current_function: &'a str,
    direct_dep_keys: BTreeSet<String>,
    direct_ask_keys: BTreeSet<String>,
    direct_calls: BTreeSet<String>,
    unresolved_calls: BTreeSet<String>,
    aliases: HashMap<String, AliasTarget>,
}

impl<'a> FunctionContext<'a> {
    fn new(global: &'a GlobalDoMap, current_function: &'a str) -> Self {
        Self {
            global,
            current_function,
            direct_dep_keys: BTreeSet::new(),
            direct_ask_keys: BTreeSet::new(),
            direct_calls: BTreeSet::new(),
            unresolved_calls: BTreeSet::new(),
            aliases: HashMap::new(),
        }
    }

    fn analyze_statements(&mut self, statements: &[Stmt]) {
        for stmt in statements {
            self.analyze_statement(stmt);
        }
    }

    fn analyze_statement(&mut self, stmt: &Stmt) {
        match stmt {
            Stmt::Expr(expr_stmt) => {
                self.analyze_expr(&expr_stmt.value);
            }
            Stmt::Return(ret_stmt) => {
                if let Some(value) = ret_stmt.value.as_deref() {
                    self.analyze_expr(value);
                }
            }
            Stmt::Assign(assign) => {
                let alias_target = self.resolve_alias_target(assign.value.as_ref());
                self.analyze_expr(assign.value.as_ref());
                for target in &assign.targets {
                    self.assign_alias_to_target(target, alias_target.clone());
                }
            }
            Stmt::AnnAssign(assign) => {
                if let Some(value) = assign.value.as_deref() {
                    let alias_target = self.resolve_alias_target(value);
                    self.analyze_expr(value);
                    self.assign_alias_to_target(&assign.target, alias_target);
                } else {
                    self.assign_alias_to_target(&assign.target, None);
                }
            }
            Stmt::AugAssign(assign) => {
                self.analyze_expr(assign.value.as_ref());
                self.clear_aliases_in_target(&assign.target);
            }
            Stmt::If(if_stmt) => {
                self.analyze_expr(&if_stmt.test);
                self.analyze_statements(&if_stmt.body);
                self.analyze_statements(&if_stmt.orelse);
            }
            Stmt::While(while_stmt) => {
                self.analyze_expr(&while_stmt.test);
                self.analyze_statements(&while_stmt.body);
                self.analyze_statements(&while_stmt.orelse);
            }
            Stmt::For(for_stmt) => {
                self.analyze_expr(&for_stmt.iter);
                self.clear_aliases_in_target(&for_stmt.target);
                self.analyze_statements(&for_stmt.body);
                self.analyze_statements(&for_stmt.orelse);
            }
            Stmt::AsyncFor(for_stmt) => {
                self.analyze_expr(&for_stmt.iter);
                self.clear_aliases_in_target(&for_stmt.target);
                self.analyze_statements(&for_stmt.body);
                self.analyze_statements(&for_stmt.orelse);
            }
            Stmt::With(with_stmt) => {
                for item in &with_stmt.items {
                    self.analyze_expr(&item.context_expr);
                    if let Some(optional) = item.optional_vars.as_deref() {
                        self.clear_aliases_in_target(optional);
                    }
                }
                self.analyze_statements(&with_stmt.body);
            }
            Stmt::AsyncWith(with_stmt) => {
                for item in &with_stmt.items {
                    self.analyze_expr(&item.context_expr);
                    if let Some(optional) = item.optional_vars.as_deref() {
                        self.clear_aliases_in_target(optional);
                    }
                }
                self.analyze_statements(&with_stmt.body);
            }
            Stmt::Try(try_stmt) => {
                self.analyze_statements(&try_stmt.body);
                for handler in &try_stmt.handlers {
                    match handler {
                        ast::ExceptHandler::ExceptHandler(handler) => {
                            if let Some(typ) = handler.type_.as_deref() {
                                self.analyze_expr(typ);
                            }
                            if let Some(name) = handler.name.as_ref() {
                                self.aliases.remove(name.as_str());
                            }
                            self.analyze_statements(&handler.body);
                        }
                    }
                }
                self.analyze_statements(&try_stmt.orelse);
                self.analyze_statements(&try_stmt.finalbody);
            }
            Stmt::Match(match_stmt) => {
                self.analyze_expr(&match_stmt.subject);
                for case in &match_stmt.cases {
                    if let Some(guard) = case.guard.as_deref() {
                        self.analyze_expr(guard);
                    }
                    self.analyze_statements(&case.body);
                }
            }
            Stmt::FunctionDef(_) | Stmt::AsyncFunctionDef(_) | Stmt::ClassDef(_) => {
                // Nested definitions create new scopes; ignore for dependency tracking.
            }
            _ => {}
        }
    }

    fn analyze_expr(&mut self, expr: &Expr) {
        match expr {
            Expr::Yield(yield_expr) => {
                if let Some(value) = yield_expr.value.as_deref() {
                    self.handle_yield_value(value);
                    self.analyze_expr(value);
                }
            }
            Expr::YieldFrom(yield_from) => {
                self.handle_yield_value(&yield_from.value);
                self.analyze_expr(&yield_from.value);
            }
            Expr::Call(call) => {
                for arg in &call.args {
                    self.analyze_expr(arg);
                }
                for keyword in &call.keywords {
                    self.analyze_expr(&keyword.value);
                }
            }
            Expr::BoolOp(expr_bool) => {
                for value in &expr_bool.values {
                    self.analyze_expr(value);
                }
            }
            Expr::NamedExpr(named_expr) => {
                let alias_target = self.resolve_alias_target(&named_expr.value);
                self.analyze_expr(&named_expr.value);
                self.assign_alias_to_target(&named_expr.target, alias_target);
            }
            Expr::BinOp(bin_op) => {
                self.analyze_expr(&bin_op.left);
                self.analyze_expr(&bin_op.right);
            }
            Expr::UnaryOp(unary_op) => {
                self.analyze_expr(&unary_op.operand);
            }
            Expr::Lambda(lambda_expr) => {
                self.analyze_expr(&lambda_expr.body);
            }
            Expr::IfExp(if_expr) => {
                self.analyze_expr(&if_expr.test);
                self.analyze_expr(&if_expr.body);
                self.analyze_expr(&if_expr.orelse);
            }
            Expr::Dict(dict_expr) => {
                for key in &dict_expr.keys {
                    if let Some(key_expr) = key.as_ref() {
                        self.analyze_expr(key_expr);
                    }
                }
                for value in &dict_expr.values {
                    self.analyze_expr(value);
                }
            }
            Expr::Set(set_expr) => {
                for value in &set_expr.elts {
                    self.analyze_expr(value);
                }
            }
            Expr::ListComp(comp) => {
                self.analyze_expr(&comp.elt);
                for generator in &comp.generators {
                    self.analyze_expr(&generator.iter);
                    for if_expr in &generator.ifs {
                        self.analyze_expr(if_expr);
                    }
                }
            }
            Expr::SetComp(comp) => {
                self.analyze_expr(&comp.elt);
                for generator in &comp.generators {
                    self.analyze_expr(&generator.iter);
                    for if_expr in &generator.ifs {
                        self.analyze_expr(if_expr);
                    }
                }
            }
            Expr::DictComp(comp) => {
                self.analyze_expr(&comp.key);
                self.analyze_expr(&comp.value);
                for generator in &comp.generators {
                    self.analyze_expr(&generator.iter);
                    for if_expr in &generator.ifs {
                        self.analyze_expr(if_expr);
                    }
                }
            }
            Expr::GeneratorExp(gen_exp) => {
                self.analyze_expr(&gen_exp.elt);
                for generator in &gen_exp.generators {
                    self.analyze_expr(&generator.iter);
                    for if_expr in &generator.ifs {
                        self.analyze_expr(if_expr);
                    }
                }
            }
            Expr::Await(await_expr) => {
                self.analyze_expr(&await_expr.value);
            }
            Expr::Compare(compare_expr) => {
                self.analyze_expr(&compare_expr.left);
                for comparator in &compare_expr.comparators {
                    self.analyze_expr(comparator);
                }
            }
            Expr::FormattedValue(formatted) => {
                self.analyze_expr(&formatted.value);
            }
            Expr::JoinedStr(joined) => {
                for value in &joined.values {
                    self.analyze_expr(value);
                }
            }
            Expr::Attribute(attr) => {
                self.analyze_expr(&attr.value);
            }
            Expr::Subscript(subscript) => {
                self.analyze_expr(&subscript.value);
                self.analyze_expr(&subscript.slice);
            }
            Expr::Starred(starred) => {
                self.analyze_expr(&starred.value);
            }
            Expr::Name(_) | Expr::Constant(_) => {}
            Expr::List(list_expr) => {
                for value in &list_expr.elts {
                    self.analyze_expr(value);
                }
            }
            Expr::Tuple(tuple_expr) => {
                for value in &tuple_expr.elts {
                    self.analyze_expr(value);
                }
            }
            Expr::Slice(slice_expr) => {
                if let Some(lower) = slice_expr.lower.as_deref() {
                    self.analyze_expr(lower);
                }
                if let Some(upper) = slice_expr.upper.as_deref() {
                    self.analyze_expr(upper);
                }
                if let Some(step) = slice_expr.step.as_deref() {
                    self.analyze_expr(step);
                }
            }
        }
    }

    fn handle_yield_value(&mut self, expr: &Expr) {
        if let Some((kind, key)) = self.extract_effect_key(expr) {
            match kind {
                EffectKind::Dep => {
                    self.direct_dep_keys.insert(key);
                }
                EffectKind::Ask => {
                    self.direct_ask_keys.insert(key);
                }
            }
            return;
        }
        if let Some(resolution) = self.resolve_function_from_expr(expr) {
            match resolution {
                CalleeResolution::Resolved(name) => {
                    if name != self.current_function {
                        self.direct_calls.insert(name);
                    }
                }
                CalleeResolution::Unresolved(label) => {
                    self.unresolved_calls.insert(label);
                }
            }
        }
    }

    fn resolve_alias_target(&self, expr: &Expr) -> Option<AliasTarget> {
        if let Some((kind, key)) = self.extract_effect_key(expr) {
            return Some(AliasTarget::Effect(kind, key));
        }
        match self.resolve_function_from_expr(expr) {
            Some(CalleeResolution::Resolved(name)) => Some(AliasTarget::DoFunction(name)),
            _ => None,
        }
    }

    fn resolve_function_from_expr(&self, expr: &Expr) -> Option<CalleeResolution> {
        match expr {
            Expr::Call(call) => self.resolve_function_from_call(call),
            Expr::Name(name) => {
                let repr = expr_to_string(expr);
                if let Some(target) = self.global.alias_target_for_simple(name.id.as_str()) {
                    return Some(CalleeResolution::Resolved(target.clone()));
                }
                if let Some(unresolved) = self.global.unresolved_alias_simple(name.id.as_str()) {
                    return Some(CalleeResolution::Unresolved(unresolved.clone()));
                }
                if let Some(alias) = self.aliases.get(name.id.as_str()) {
                    if let AliasTarget::DoFunction(qualified) = alias {
                        return Some(CalleeResolution::Resolved(qualified.clone()));
                    }
                }
                if let Some(candidates) = self.global.candidates_for_simple(name.id.as_str()) {
                    if candidates.len() == 1 {
                        return Some(CalleeResolution::Resolved(candidates[0].clone()));
                    } else if !candidates.is_empty() {
                        return Some(CalleeResolution::Unresolved(repr));
                    }
                }
                None
            }
            Expr::Attribute(attr) => {
                if attr.attr.as_str() == "partial" {
                    return self.resolve_function_from_expr(&attr.value);
                }
                if let Some(target) = self
                    .global
                    .alias_target_for_qualified(&expr_to_string(expr))
                    .or_else(|| self.global.alias_target_for_simple(attr.attr.as_str()))
                {
                    return Some(CalleeResolution::Resolved(target.clone()));
                }
                if let Some(unresolved) = self
                    .global
                    .unresolved_alias_qualified(&expr_to_string(expr))
                    .or_else(|| self.global.unresolved_alias_simple(attr.attr.as_str()))
                {
                    return Some(CalleeResolution::Unresolved(unresolved.clone()));
                }
                if let Expr::Name(base_name) = attr.value.as_ref() {
                    if let Some(alias) = self.aliases.get(base_name.id.as_str()) {
                        if let AliasTarget::DoFunction(qualified) = alias {
                            return Some(CalleeResolution::Resolved(qualified.clone()));
                        }
                    }
                }
                let segments = attribute_path_segments(expr);
                if segments.is_empty() {
                    return None;
                }
                let joined = segments.join(".");
                if let Some(stub) = self.global.by_qualified.get(&joined) {
                    return Some(CalleeResolution::Resolved(stub.qualified_name.clone()));
                }
                if let Some(last) = segments.last() {
                    if let Some(candidates) = self.global.candidates_for_simple(last) {
                        if candidates.len() == 1 {
                            return Some(CalleeResolution::Resolved(candidates[0].clone()));
                        } else if !candidates.is_empty() {
                            return Some(CalleeResolution::Unresolved(expr_to_string(expr)));
                        }
                    }
                }
                None
            }
            _ => None,
        }
    }

    fn resolve_function_from_call(&self, call: &ast::ExprCall) -> Option<CalleeResolution> {
        if self.extract_effect_key_from_call(call).is_some() {
            return None;
        }
        self.resolve_function_from_expr(call.func.as_ref())
    }

    fn extract_effect_key(&self, expr: &Expr) -> Option<(EffectKind, String)> {
        match expr {
            Expr::Call(call) => self.extract_effect_key_from_call(call),
            Expr::Name(name) => self
                .aliases
                .get(name.id.as_str())
                .and_then(|alias| match alias {
                    AliasTarget::Effect(kind, key) => Some((*kind, key.clone())),
                    _ => None,
                }),
            _ => None,
        }
    }

    fn extract_effect_key_from_call(&self, call: &ast::ExprCall) -> Option<(EffectKind, String)> {
        let kind = identify_effect_kind(call.func.as_ref())?;
        if let Some(first_arg) = call.args.first() {
            if let Expr::Constant(constant) = first_arg {
                if let Constant::Str(value) = &constant.value {
                    return Some((kind, value.clone()));
                }
            }
        }
        for keyword in &call.keywords {
            if keyword.arg.as_deref() == Some("key") {
                if let Expr::Constant(constant) = &keyword.value {
                    if let Constant::Str(inner) = &constant.value {
                        return Some((kind, inner.clone()));
                    }
                }
            }
        }
        None
    }

    fn assign_alias_to_target(&mut self, target: &Expr, alias: Option<AliasTarget>) {
        match target {
            Expr::Name(name) => {
                if let Some(alias_target) = alias {
                    self.aliases.insert(name.id.to_string(), alias_target);
                } else {
                    self.aliases.remove(name.id.as_str());
                }
            }
            Expr::Tuple(tuple) => {
                for element in &tuple.elts {
                    self.assign_alias_to_target(element, None);
                }
            }
            Expr::List(list) => {
                for element in &list.elts {
                    self.assign_alias_to_target(element, None);
                }
            }
            Expr::Starred(starred) => {
                self.assign_alias_to_target(&starred.value, None);
            }
            Expr::Subscript(subscript) => {
                self.assign_alias_to_target(&subscript.value, None);
            }
            Expr::Attribute(attr) => {
                self.assign_alias_to_target(&attr.value, None);
            }
            _ => {}
        }
    }

    fn clear_aliases_in_target(&mut self, target: &Expr) {
        self.assign_alias_to_target(target, None);
    }
}

fn attribute_path_segments(expr: &Expr) -> Vec<String> {
    match expr {
        Expr::Attribute(attr) => {
            let mut segments = attribute_path_segments(&attr.value);
            segments.push(attr.attr.to_string());
            segments
        }
        Expr::Name(name) => vec![name.id.to_string()],
        _ => Vec::new(),
    }
}

fn is_do_wrapper_marker(expr: &Expr) -> bool {
    match expr {
        Expr::Name(name) => name.id.as_str() == "do_wrapper",
        Expr::Attribute(attr) => {
            attr.attr.as_str() == "do_wrapper" || is_do_wrapper_marker(&attr.value)
        }
        Expr::Call(call) => is_do_wrapper_marker(&call.func),
        _ => false,
    }
}

fn is_dep_callable(expr: &Expr) -> bool {
    match expr {
        Expr::Name(name) => name.id.as_str() == "Dep",
        Expr::Attribute(attr) => attr.attr.as_str() == "Dep" || is_dep_callable(&attr.value),
        Expr::Call(call) => is_dep_callable(call.func.as_ref()),
        _ => false,
    }
}

fn is_ask_callable(expr: &Expr) -> bool {
    match expr {
        Expr::Name(name) => name.id.as_str() == "Ask",
        Expr::Attribute(attr) => attr.attr.as_str() == "Ask" || is_ask_callable(&attr.value),
        Expr::Call(call) => is_ask_callable(call.func.as_ref()),
        _ => false,
    }
}

fn identify_effect_kind(expr: &Expr) -> Option<EffectKind> {
    if is_dep_callable(expr) {
        return Some(EffectKind::Dep);
    }
    if is_ask_callable(expr) {
        return Some(EffectKind::Ask);
    }
    None
}

pub fn analyze_dependencies(root: impl AsRef<Path>) -> Result<Vec<FunctionDependency>> {
    let root = root.as_ref();
    let canonical_root = root.canonicalize().unwrap_or_else(|_| root.to_path_buf());

    let mut wrapper_factories = collect_do_wrappers(&canonical_root)?;
    wrapper_factories.extend(DEFAULT_DO_WRAPPERS.iter().map(|s| s.to_string()));
    let (stubs, aliases) = collect_do_functions(&canonical_root, &wrapper_factories)?;
    if stubs.is_empty() && aliases.is_empty() {
        return Ok(Vec::new());
    }

    let global_map = GlobalDoMap::new(&stubs, &aliases);

    let mut by_file: HashMap<PathBuf, Vec<DoFunctionStub>> = HashMap::new();
    for stub in &stubs {
        by_file
            .entry(stub.file_path.clone())
            .or_default()
            .push(stub.clone());
    }

    let mut direct_deps: HashMap<String, BTreeSet<String>> = HashMap::new();
    let mut direct_asks: HashMap<String, BTreeSet<String>> = HashMap::new();
    let mut call_graph: HashMap<String, BTreeSet<String>> = HashMap::new();
    let mut unresolved_calls: HashMap<String, BTreeSet<String>> = HashMap::new();

    for (file_path, stubs) in by_file {
        let source = fs::read_to_string(&file_path)
            .with_context(|| format!("Failed to read {}", file_path.display()))?;

        if stubs.is_empty() {
            continue;
        }

        let module = parse(&source, Mode::Module, &file_path.to_string_lossy())
            .with_context(|| format!("Failed to parse {}", file_path.display()))?;

        let Mod::Module(module) = module else {
            continue;
        };

        let line_index = LineIndex::new(&source);

        for stub in stubs {
            if let Some(body) = find_function_body(&module.body, &stub, &line_index) {
                let mut ctx = FunctionContext::new(&global_map, &stub.qualified_name);
                ctx.analyze_statements(body);
                let FunctionContext {
                    direct_dep_keys,
                    direct_ask_keys,
                    direct_calls,
                    unresolved_calls: ctx_unresolved_calls,
                    ..
                } = ctx;
                direct_deps.insert(stub.qualified_name.clone(), direct_dep_keys);
                direct_asks.insert(stub.qualified_name.clone(), direct_ask_keys);
                call_graph.insert(stub.qualified_name.clone(), direct_calls);
                unresolved_calls.insert(stub.qualified_name.clone(), ctx_unresolved_calls);
            } else {
                direct_deps
                    .entry(stub.qualified_name.clone())
                    .or_insert_with(BTreeSet::new);
                direct_asks
                    .entry(stub.qualified_name.clone())
                    .or_insert_with(BTreeSet::new);
                call_graph
                    .entry(stub.qualified_name.clone())
                    .or_insert_with(BTreeSet::new);
                unresolved_calls
                    .entry(stub.qualified_name.clone())
                    .or_insert_with(BTreeSet::new);
            }
        }
    }

    let mut memo_deps: HashMap<String, BTreeSet<String>> = HashMap::new();
    let mut memo_asks: HashMap<String, BTreeSet<String>> = HashMap::new();
    let mut stack: HashSet<String> = HashSet::new();

    let mut results = Vec::new();

    for (qualified, stub) in &global_map.by_qualified {
        let direct_dep = direct_deps
            .get(qualified)
            .cloned()
            .unwrap_or_else(BTreeSet::new);
        let all_dep = gather_all_effects(
            qualified,
            &direct_deps,
            &call_graph,
            &mut memo_deps,
            &mut stack,
        );
        let direct_ask = direct_asks
            .get(qualified)
            .cloned()
            .unwrap_or_else(BTreeSet::new);
        let all_ask = gather_all_effects(
            qualified,
            &direct_asks,
            &call_graph,
            &mut memo_asks,
            &mut stack,
        );
        let calls = call_graph
            .get(qualified)
            .cloned()
            .unwrap_or_else(BTreeSet::new);
        let unresolved = unresolved_calls
            .get(qualified)
            .cloned()
            .unwrap_or_else(BTreeSet::new);

        results.push(FunctionDependency {
            qualified_name: qualified.clone(),
            file_path: stub.file_path.to_string_lossy().to_string(),
            line: stub.line,
            direct_dep_keys: direct_dep.into_iter().collect(),
            all_dep_keys: all_dep.into_iter().collect(),
            direct_ask_keys: direct_ask.into_iter().collect(),
            all_ask_keys: all_ask.into_iter().collect(),
            direct_calls: calls.into_iter().collect(),
            unresolved_calls: unresolved.into_iter().collect(),
        });
    }

    let mut results_map: BTreeMap<String, FunctionDependency> = results
        .iter()
        .cloned()
        .map(|entry| (entry.qualified_name.clone(), entry))
        .collect();

    for alias in aliases {
        let mut direct_calls = Vec::new();
        let mut unresolved_calls = Vec::new();
        let mut all_dep_keys = Vec::new();
        let mut all_ask_keys = Vec::new();

        if let Some(target) = global_map.alias_target_for_qualified(&alias.qualified_name) {
            direct_calls.push(target.clone());
            if let Some(target_entry) = results_map.get(target) {
                all_dep_keys = target_entry.all_dep_keys.clone();
                all_ask_keys = target_entry.all_ask_keys.clone();
            }
        } else if let Some(unresolved) =
            global_map.unresolved_alias_qualified(&alias.qualified_name)
        {
            unresolved_calls.push(unresolved.clone());
        }

        let entry = FunctionDependency {
            qualified_name: alias.qualified_name.clone(),
            file_path: alias.file_path.to_string_lossy().to_string(),
            line: alias.line,
            direct_dep_keys: Vec::new(),
            all_dep_keys,
            direct_ask_keys: Vec::new(),
            all_ask_keys,
            direct_calls,
            unresolved_calls,
        };

        results_map.insert(alias.qualified_name.clone(), entry.clone());
        results.push(entry);
    }

    results.sort_by(|a, b| a.qualified_name.cmp(&b.qualified_name));
    Ok(results)
}

fn gather_all_effects(
    function: &str,
    direct_deps: &HashMap<String, BTreeSet<String>>,
    call_graph: &HashMap<String, BTreeSet<String>>,
    memo: &mut HashMap<String, BTreeSet<String>>,
    stack: &mut HashSet<String>,
) -> BTreeSet<String> {
    if let Some(cached) = memo.get(function) {
        return cached.clone();
    }

    if !stack.insert(function.to_string()) {
        return BTreeSet::new();
    }

    let mut result = direct_deps
        .get(function)
        .cloned()
        .unwrap_or_else(BTreeSet::new);

    if let Some(callees) = call_graph.get(function) {
        for callee in callees {
            if stack.contains(callee) {
                continue;
            }
            let deps = gather_all_effects(callee, direct_deps, call_graph, memo, stack);
            result.extend(deps);
        }
    }

    stack.remove(function);
    memo.insert(function.to_string(), result.clone());
    result
}

fn find_function_body<'a>(
    statements: &'a [Stmt],
    stub: &DoFunctionStub,
    line_index: &LineIndex,
) -> Option<&'a [Stmt]> {
    for stmt in statements {
        match stmt {
            Stmt::FunctionDef(func) if func.name.as_str() == stub.simple_name => {
                let line = line_index.line_number(func.range.start());
                if line == stub.line {
                    return Some(&func.body);
                }
            }
            Stmt::AsyncFunctionDef(func) if func.name.as_str() == stub.simple_name => {
                let line = line_index.line_number(func.range.start());
                if line == stub.line {
                    return Some(&func.body);
                }
            }
            _ => {}
        }
    }
    None
}

fn collect_do_wrappers(root: &Path) -> Result<HashSet<String>> {
    let mut wrappers = HashSet::new();
    for entry in WalkDir::new(root)
        .into_iter()
        .filter_entry(|e| should_descend(e))
    {
        let entry = match entry {
            Ok(entry) => entry,
            Err(_) => continue,
        };
        if entry.file_type().is_file() && is_python_file(entry.path()) {
            let mut file_wrappers = collect_do_wrappers_from_file(entry.path(), root)?;
            wrappers.extend(file_wrappers.drain(..));
        }
    }
    Ok(wrappers)
}

fn collect_do_wrappers_from_file(path: &Path, root: &Path) -> Result<Vec<String>> {
    let source =
        fs::read_to_string(path).with_context(|| format!("Failed to read {}", path.display()))?;

    let module = parse(&source, Mode::Module, &path.to_string_lossy())
        .with_context(|| format!("Failed to parse {}", path.display()))?;

    let Mod::Module(module) = module else {
        return Ok(Vec::new());
    };

    let module_path = compute_module_path(root, path);
    let mut wrappers = Vec::new();

    for stmt in &module.body {
        match stmt {
            Stmt::FunctionDef(func) => {
                if func.decorator_list.iter().any(is_do_wrapper_marker) {
                    wrappers.push(func.name.to_string());
                    if !module_path.is_empty() {
                        wrappers.push(format!("{}.{}", module_path, func.name));
                    }
                }
            }
            Stmt::AsyncFunctionDef(func) => {
                if func.decorator_list.iter().any(is_do_wrapper_marker) {
                    wrappers.push(func.name.to_string());
                    if !module_path.is_empty() {
                        wrappers.push(format!("{}.{}", module_path, func.name));
                    }
                }
            }
            _ => {}
        }
    }

    Ok(wrappers)
}

fn collect_do_functions(
    root: &Path,
    wrappers: &HashSet<String>,
) -> Result<(Vec<DoFunctionStub>, Vec<DoAlias>)> {
    let mut stubs = Vec::new();
    let mut aliases = Vec::new();
    for entry in WalkDir::new(root)
        .into_iter()
        .filter_entry(|e| should_descend(e))
    {
        let entry = match entry {
            Ok(entry) => entry,
            Err(_) => continue,
        };
        if entry.file_type().is_file() && is_python_file(entry.path()) {
            let (mut file_stubs, mut file_aliases) =
                collect_do_functions_from_file(entry.path(), root, wrappers)?;
            stubs.append(&mut file_stubs);
            aliases.append(&mut file_aliases);
        }
    }
    Ok((stubs, aliases))
}

fn collect_do_functions_from_file(
    path: &Path,
    root: &Path,
    wrappers: &HashSet<String>,
) -> Result<(Vec<DoFunctionStub>, Vec<DoAlias>)> {
    let source =
        fs::read_to_string(path).with_context(|| format!("Failed to read {}", path.display()))?;

    let module = parse(&source, Mode::Module, &path.to_string_lossy())
        .with_context(|| format!("Failed to parse {}", path.display()))?;

    let Mod::Module(module) = module else {
        return Ok((Vec::new(), Vec::new()));
    };

    let module_path = compute_module_path(root, path);
    let line_index = LineIndex::new(&source);

    let mut stubs = Vec::new();
    let mut aliases = Vec::new();

    for stmt in &module.body {
        match stmt {
            Stmt::FunctionDef(func) => {
                if func.decorator_list.iter().any(is_do_decorator) {
                    let qualified = if module_path.is_empty() {
                        func.name.to_string()
                    } else {
                        format!("{}.{}", module_path, func.name)
                    };
                    let line = line_index.line_number(func.range.start());
                    stubs.push(DoFunctionStub {
                        qualified_name: qualified,
                        simple_name: func.name.to_string(),
                        file_path: path.to_path_buf(),
                        line,
                    });
                }
            }
            Stmt::AsyncFunctionDef(func) => {
                if func.decorator_list.iter().any(is_do_decorator) {
                    let qualified = if module_path.is_empty() {
                        func.name.to_string()
                    } else {
                        format!("{}.{}", module_path, func.name)
                    };
                    let line = line_index.line_number(func.range.start());
                    stubs.push(DoFunctionStub {
                        qualified_name: qualified,
                        simple_name: func.name.to_string(),
                        file_path: path.to_path_buf(),
                        line,
                    });
                }
            }
            Stmt::Assign(assign) => {
                let mut detected =
                    collect_aliases_from_assign(assign, wrappers, &module_path, path, &line_index);
                aliases.append(&mut detected);
            }
            Stmt::AnnAssign(assign) => {
                if let Some(value) = assign.value.as_deref() {
                    let line = line_index.line_number(assign.range.start());
                    if let Some(alias) = collect_alias_for_target(
                        assign.target.as_ref(),
                        value,
                        wrappers,
                        &module_path,
                        path,
                        line,
                    ) {
                        aliases.push(alias);
                    }
                }
            }
            _ => {}
        }
    }

    Ok((stubs, aliases))
}

fn collect_aliases_from_assign(
    assign: &ast::StmtAssign,
    wrappers: &HashSet<String>,
    module_path: &str,
    path: &Path,
    line_index: &LineIndex,
) -> Vec<DoAlias> {
    let mut aliases = Vec::new();
    let line = line_index.line_number(assign.range.start());
    for target in &assign.targets {
        if let Some(alias) =
            collect_alias_for_target(target, &assign.value, wrappers, module_path, path, line)
        {
            aliases.push(alias);
        }
    }
    aliases
}

fn collect_alias_for_target(
    target: &Expr,
    value: &Expr,
    wrappers: &HashSet<String>,
    module_path: &str,
    path: &Path,
    line: usize,
) -> Option<DoAlias> {
    let Expr::Name(name) = target else {
        return None;
    };

    let (target_expr, target_simple) = extract_wrapper_target(value, wrappers)?;

    let qualified_name = if module_path.is_empty() {
        name.id.to_string()
    } else {
        format!("{}.{}", module_path, name.id)
    };

    Some(DoAlias {
        qualified_name,
        simple_name: name.id.to_string(),
        module_path: module_path.to_string(),
        target_expr,
        target_simple,
        file_path: path.to_path_buf(),
        line,
    })
}

fn extract_wrapper_target(
    expr: &Expr,
    wrappers: &HashSet<String>,
) -> Option<(String, Option<String>)> {
    if let Expr::Call(call) = expr {
        if is_wrapper_expression(&call.func, wrappers) {
            if let Some(arg) = call.args.first() {
                return Some((expr_to_string(arg), extract_simple_name(arg)));
            }
        }
    }
    None
}

fn extract_simple_name(expr: &Expr) -> Option<String> {
    match expr {
        Expr::Name(name) => Some(name.id.to_string()),
        Expr::Attribute(attr) => Some(attr.attr.to_string()),
        _ => None,
    }
}

fn is_wrapper_expression(expr: &Expr, wrappers: &HashSet<String>) -> bool {
    match expr {
        Expr::Name(name) => wrappers.contains(name.id.as_str()),
        Expr::Attribute(attr) => {
            let attr_name = attr.attr.to_string();
            if wrappers.contains(attr_name.as_str()) {
                return true;
            }
            let path = attribute_path_segments(expr).join(".");
            wrappers.contains(&path)
        }
        Expr::Call(call) => is_wrapper_expression(call.func.as_ref(), wrappers),
        _ => false,
    }
}

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};

use crate::{
    effect_registry::EffectRegistry,
    function_summary::{ArgumentValue, CallArgument, CallEdge, SummaryCollector},
    resolver, source, syntax, EffectTreeNode, EffectUsage, NodeKind, SourceSpan, TargetKind,
};
use anyhow::Result;
use rustpython_ast::{self as ast, Stmt};
use rustpython_parser::{parse, Mode};
use tree_sitter::{Node, Tree};

pub struct SummarizedEffects {
    pub label: String,
    pub effects: Vec<EffectUsage>,
    pub effect_nodes: Vec<EffectTreeNode>,
    pub warnings: Vec<String>,
    pub root_span: Option<SourceSpan>,
}

impl SummarizedEffects {
    pub fn empty() -> Self {
        Self {
            label: String::new(),
            effects: Vec::new(),
            effect_nodes: Vec::new(),
            warnings: Vec::new(),
            root_span: None,
        }
    }
}

#[derive(Debug, Clone)]
enum CallTarget {
    Function { module: String, symbol: String },
    Method { module: String, class_name: String, method_name: String },
}

pub fn summarize_target(
    target_kind: TargetKind,
    symbol: &str,
    module_name: &str,
    source_text: &str,
    tree: &Tree,
    file_path: &Path,
    definition_span: Option<&SourceSpan>,
    root: &Path,
    registry: &EffectRegistry,
) -> SummarizedEffects {
    let mut context = ModuleContext::new(root, registry);
    let _ = context.insert_preloaded(
        module_name,
        source_text,
        tree.clone(),
        file_path.to_path_buf(),
    );

    let mut visited = BTreeSet::new();
    match target_kind {
        TargetKind::KleisliProgram => {
            summarize_function(&mut context, module_name, symbol, &mut visited)
        }
        TargetKind::ProgramValue => summarize_program(
            &mut context,
            module_name,
            symbol,
            definition_span,
            &mut visited,
        ),
        TargetKind::Other => summarize_function(&mut context, module_name, symbol, &mut visited),
    }
}

fn summarize_function(
    context: &mut ModuleContext,
    module_name: &str,
    symbol: &str,
    visited: &mut BTreeSet<String>,
) -> SummarizedEffects {
    let visit_key = format!("{module_name}::{symbol}");
    if !visited.insert(visit_key.clone()) {
        return SummarizedEffects {
            label: format!("fn {symbol}"),
            warnings: vec![format!("Recursive call detected for '{}'", visit_key)],
            ..SummarizedEffects::empty()
        };
    }

    let Some(module) = context.ensure_module(module_name).cloned() else {
        visited.remove(&visit_key);
        return SummarizedEffects {
            label: format!("fn {symbol}"),
            warnings: vec![format!(
                "Unable to load module '{}' for '{}'",
                module_name, symbol
            )],
            ..SummarizedEffects::empty()
        };
    };

    let Some(function_node) = find_function_node(&module.tree, &module.source, symbol) else {
        visited.remove(&visit_key);
        return SummarizedEffects {
            label: format!("fn {symbol}"),
            warnings: vec![format!(
                "Unable to locate function definition for '{symbol}' in module '{}'",
                module_name
            )],
            ..SummarizedEffects::empty()
        };
    };

    let collector = SummaryCollector::new(context.registry, &module.source, &module.file_path);
    let block_summary = collector.summarize_node(function_node);

    let mut warnings = block_summary.warnings.clone();
    let mut effects = block_summary.local_effects.clone();

    let mut effect_nodes: Vec<EffectTreeNode> = block_summary
        .local_effects
        .iter()
        .enumerate()
        .map(|(index, effect)| EffectTreeNode {
            kind: NodeKind::Effect,
            label: format!("yield#{index}: {}", effect.key),
            effects: vec![effect.key.clone()],
            span: effect.span.clone(),
            children: Vec::new(),
        })
        .collect();

    for call in &block_summary.calls {
        let (call_effects, call_nodes, call_warnings) =
            collect_call_effects(context, module_name, call, visited);
        effects.extend(call_effects);
        warnings.extend(call_warnings);
        effect_nodes.extend(call_nodes);
    }

    visited.remove(&visit_key);

    SummarizedEffects {
        label: format!("fn {symbol}"),
        effects,
        effect_nodes,
        warnings,
        root_span: Some(span_from_node(
            function_node,
            &module.source,
            &module.file_path,
        )),
    }
}

fn summarize_program(
    context: &mut ModuleContext,
    module_name: &str,
    symbol: &str,
    definition_span: Option<&SourceSpan>,
    visited: &mut BTreeSet<String>,
) -> SummarizedEffects {
    let visit_key = format!("{module_name}::program::{symbol}");
    if !visited.insert(visit_key.clone()) {
        return SummarizedEffects {
            label: format!("program {symbol}"),
            warnings: vec![format!("Recursive traversal for program '{}'", visit_key)],
            ..SummarizedEffects::empty()
        };
    }

    let Some(module) = context.ensure_module(module_name).cloned() else {
        visited.remove(&visit_key);
        return SummarizedEffects {
            label: format!("program {symbol}"),
            warnings: vec![format!(
                "Unable to load module '{}' for program '{}'",
                module_name, symbol
            )],
            ..SummarizedEffects::empty()
        };
    };

    let Some(assignment_node) =
        find_assignment_node(&module.tree, &module.source, symbol, definition_span)
    else {
        visited.remove(&visit_key);
        return SummarizedEffects {
            label: format!("program {symbol}"),
            warnings: vec![format!(
                "Unable to locate assignment for program value '{symbol}'"
            )],
            ..SummarizedEffects::empty()
        };
    };

    let collector = SummaryCollector::new(context.registry, &module.source, &module.file_path);
    let block_summary = collector.summarize_node(assignment_node);

    let mut warnings = block_summary.warnings.clone();
    let mut effects = block_summary.local_effects.clone();

    let mut effect_nodes: Vec<EffectTreeNode> = block_summary
        .local_effects
        .iter()
        .enumerate()
        .map(|(index, effect)| EffectTreeNode {
            kind: NodeKind::Effect,
            label: format!("step#{index}: {}", effect.key),
            effects: vec![effect.key.clone()],
            span: effect.span.clone(),
            children: Vec::new(),
        })
        .collect();

    for call in block_summary.calls.iter() {
        let (call_effects, call_nodes, call_warnings) =
            collect_call_effects(context, module_name, call, visited);
        effects.extend(call_effects);
        warnings.extend(call_warnings);
        effect_nodes.extend(call_nodes);
    }

    visited.remove(&visit_key);

    SummarizedEffects {
        label: format!("program {symbol}"),
        effects,
        effect_nodes,
        warnings,
        root_span: definition_span.cloned().or_else(|| {
            Some(span_from_node(
                assignment_node,
                &module.source,
                &module.file_path,
            ))
        }),
    }
}

fn summarize_method(
    context: &mut ModuleContext,
    module_name: &str,
    class_name: &str,
    method_name: &str,
    visited: &mut BTreeSet<String>,
) -> SummarizedEffects {
    let visit_key = format!("{module_name}::{class_name}.{method_name}");
    if !visited.insert(visit_key.clone()) {
        return SummarizedEffects {
            label: format!("fn {class_name}.{method_name}"),
            warnings: vec![format!("Recursive call detected for '{}'", visit_key)],
            ..SummarizedEffects::empty()
        };
    }

    let Some(module) = context.ensure_module(module_name).cloned() else {
        visited.remove(&visit_key);
        return SummarizedEffects {
            label: format!("fn {class_name}.{method_name}"),
            warnings: vec![format!(
                "Unable to load module '{}' for '{}.{}'",
                module_name, class_name, method_name
            )],
            ..SummarizedEffects::empty()
        };
    };

    let Some(method_node) =
        find_method_node(&module.tree, &module.source, class_name, method_name)
    else {
        visited.remove(&visit_key);
        return SummarizedEffects {
            label: format!("fn {class_name}.{method_name}"),
            warnings: vec![format!(
                "Unable to locate method '{}.{}' in module '{}'",
                class_name, method_name, module_name
            )],
            ..SummarizedEffects::empty()
        };
    };

    let collector = SummaryCollector::new(context.registry, &module.source, &module.file_path);
    let block_summary = collector.summarize_node(method_node);

    let mut warnings = block_summary.warnings.clone();
    let mut effects = block_summary.local_effects.clone();

    let mut effect_nodes: Vec<EffectTreeNode> = block_summary
        .local_effects
        .iter()
        .enumerate()
        .map(|(index, effect)| EffectTreeNode {
            kind: NodeKind::Effect,
            label: format!("yield#{index}: {}", effect.key),
            effects: vec![effect.key.clone()],
            span: effect.span.clone(),
            children: Vec::new(),
        })
        .collect();

    for call in &block_summary.calls {
        let (call_effects, call_nodes, call_warnings) =
            collect_call_effects(context, module_name, call, visited);
        effects.extend(call_effects);
        warnings.extend(call_warnings);
        effect_nodes.extend(call_nodes);
    }

    visited.remove(&visit_key);

    SummarizedEffects {
        label: format!("fn {class_name}.{method_name}"),
        effects,
        effect_nodes,
        warnings,
        root_span: Some(span_from_node(
            method_node,
            &module.source,
            &module.file_path,
        )),
    }
}

struct ModuleContext<'a> {
    root: &'a Path,
    registry: &'a EffectRegistry,
    cache: BTreeMap<String, ModuleData>,
}

impl<'a> ModuleContext<'a> {
    fn new(root: &'a Path, registry: &'a EffectRegistry) -> Self {
        Self {
            root,
            registry,
            cache: BTreeMap::new(),
        }
    }

    fn insert_preloaded(
        &mut self,
        module: &str,
        source_text: &str,
        tree: Tree,
        file_path: PathBuf,
    ) -> Result<()> {
        let data = ModuleData::from_parts(module, file_path, source_text.to_string(), tree)?;
        self.cache.insert(module.to_string(), data);
        Ok(())
    }

    fn ensure_module(&mut self, module: &str) -> Option<&ModuleData> {
        if !self.cache.contains_key(module) {
            let path = resolver::resolve_module_file(self.root, module).ok()?;
            let source = fs::read_to_string(&path).ok()?;
            let tree = syntax::parse_module(&source).ok()?;
            let data = ModuleData::from_parts(module, path, source, tree).ok()?;
            self.cache.insert(module.to_string(), data);
        }
        self.cache.get(module)
    }

    fn resolve_call_target_internal(
        &mut self,
        module: &str,
        name: &str,
        visited: &mut BTreeSet<(String, String)>,
    ) -> Option<(String, String)> {
        let key = (module.to_string(), name.to_string());
        if !visited.insert(key.clone()) {
            return None;
        }

        let module_data = self.ensure_module(module)?.clone();
        if module_data.functions.contains(name) {
            return Some((module.to_string(), name.to_string()));
        }
        if let Some(target) = module_data.imports.get(name) {
            let next_module = if target.module.is_empty() {
                module.to_string()
            } else {
                target.module.clone()
            };
            if let Some(symbol) = &target.symbol {
                if let Some(resolved) =
                    self.resolve_call_target_internal(&next_module, symbol, visited)
                {
                    return Some(resolved);
                }
            } else if module_data.functions.contains(name) {
                return Some((module.to_string(), name.to_string()));
            }
        }
        None
    }

    fn resolve_call_edge(&mut self, module: &str, call: &CallEdge) -> Vec<CallTarget> {
        let mut visited = BTreeSet::new();
        let mut targets = Vec::new();
        if let Some(callee) = call.callee.as_deref() {
            if let Some(object) = call.object.as_deref() {
                targets.extend(self.resolve_attribute_call(module, object, callee, &mut visited));
            } else if let Some((module_name, symbol)) =
                self.resolve_call_target_internal(module, callee, &mut visited)
            {
                targets.push(CallTarget::Function {
                    module: module_name,
                    symbol,
                });
            }
        }
        targets
    }

    fn resolve_argument_value(&mut self, module: &str, argument: &CallArgument) -> Vec<CallTarget> {
        let mut visited = BTreeSet::new();
        match &argument.value {
            ArgumentValue::Identifier(name) => self
                .resolve_call_target_internal(module, name, &mut visited)
                .map(|(module_name, symbol)| vec![CallTarget::Function {
                    module: module_name,
                    symbol,
                }])
                .unwrap_or_default(),
            ArgumentValue::Attribute { object: Some(object), attribute } => {
                self.resolve_attribute_call(
                    module,
                    object.as_str(),
                    attribute.as_str(),
                    &mut visited,
                )
            }
            ArgumentValue::Attribute { object: None, attribute } => self
                .resolve_call_target_internal(module, attribute.as_str(), &mut visited)
                .map(|(module_name, symbol)| vec![CallTarget::Function {
                    module: module_name,
                    symbol,
                }])
                .unwrap_or_default(),
            _ => Vec::new(),
        }
    }

    fn resolve_attribute_call(
        &mut self,
        module: &str,
        object: &str,
        attribute: &str,
        visited: &mut BTreeSet<(String, String)>,
    ) -> Vec<CallTarget> {
        if object == "Program" {
            return Vec::new();
        }

        let mut targets = Vec::new();
        if let Some(target) = self.resolve_method_target(module, object, attribute, visited) {
            targets.push(target);
        }

        if let Some(mut resolved) =
            self.resolve_imported_attribute(module, object, attribute, visited)
        {
            targets.append(&mut resolved);
        }

        targets
    }

    fn resolve_imported_attribute(
        &mut self,
        module: &str,
        object: &str,
        attribute: &str,
        visited: &mut BTreeSet<(String, String)>,
    ) -> Option<Vec<CallTarget>> {
        let module_data = self.ensure_module(module)?.clone();
        let parts: Vec<&str> = object.split('.').collect();
        if parts.is_empty() {
            return None;
        }

        let base = parts[0];
        if let Some(import_target) = module_data.imports.get(base) {
            if import_target.symbol.is_none() {
                let mut target_module = if import_target.module.is_empty() {
                    module.to_string()
                } else {
                    import_target.module.clone()
                };
                for part in parts.iter().skip(1) {
                    target_module = if target_module.is_empty() {
                        (*part).to_string()
                    } else {
                        format!("{target_module}.{part}")
                    };
                }

                if let Some((module_name, symbol)) =
                    self.resolve_call_target_internal(&target_module, attribute, visited)
                {
                    return Some(vec![CallTarget::Function {
                        module: module_name,
                        symbol,
                    }]);
                }
            }
        } else if let Some((module_name, symbol)) =
            self.resolve_call_target_internal(object, attribute, visited)
        {
            return Some(vec![CallTarget::Function {
                module: module_name,
                symbol,
            }]);
        }

        None
    }

    fn resolve_method_target(
        &mut self,
        module: &str,
        class_name: &str,
        method_name: &str,
        visited: &mut BTreeSet<(String, String)>,
    ) -> Option<CallTarget> {
        let key = (format!("{module}::{class_name}"), method_name.to_string());
        if !visited.insert(key.clone()) {
            return None;
        }

        let module_data = self.ensure_module(module)?.clone();
        if let Some(methods) = module_data.methods.get(class_name) {
            if methods.contains(method_name) {
                return Some(CallTarget::Method {
                    module: module.to_string(),
                    class_name: class_name.to_string(),
                    method_name: method_name.to_string(),
                });
            }
        }

        None
    }
}

fn collect_call_effects(
    context: &mut ModuleContext,
    module_name: &str,
    call: &CallEdge,
    visited: &mut BTreeSet<String>,
) -> (Vec<EffectUsage>, Vec<EffectTreeNode>, Vec<String>) {
    let mut targets = context.resolve_call_edge(module_name, call);

    if call.object.as_deref() == Some("Program") && call.callee.as_deref() == Some("traverse") {
        for argument in &call.arguments {
            targets.extend(context.resolve_argument_value(module_name, argument));
        }
    }

    let mut seen = BTreeSet::new();
    let mut dedup_targets = Vec::new();
    for target in targets {
        let key = call_target_key(&target);
        if seen.insert(key) {
            dedup_targets.push(target);
        }
    }

    let mut effects = Vec::new();
    let mut nodes = Vec::new();
    let mut warnings = Vec::new();

    for target in dedup_targets {
        let summary = match &target {
            CallTarget::Function { module, symbol } => {
                summarize_function(context, module, symbol, visited)
            }
            CallTarget::Method {
                module,
                class_name,
                method_name,
            } => summarize_method(context, module, class_name, method_name, visited),
        };

        warnings.extend(summary.warnings.clone());
        effects.extend(summary.effects.clone());

        let label = if summary.label.is_empty() {
            match &target {
                CallTarget::Function { symbol, .. } => format!("fn {symbol}"),
                CallTarget::Method {
                    class_name,
                    method_name,
                    ..
                } => format!("fn {class_name}.{method_name}"),
            }
        } else {
            summary.label.clone()
        };

        let node = EffectTreeNode {
            kind: NodeKind::Function,
            label,
            effects: summary
                .effects
                .iter()
                .map(|effect| effect.key.clone())
                .collect(),
            span: summary.root_span.clone(),
            children: summary.effect_nodes,
        };
        nodes.push(node);
    }

    (effects, nodes, warnings)
}

fn call_target_key(target: &CallTarget) -> String {
    match target {
        CallTarget::Function { module, symbol } => format!("fn::{module}::{symbol}"),
        CallTarget::Method {
            module,
            class_name,
            method_name,
        } => format!("method::{module}::{class_name}.{method_name}"),
    }
}

#[derive(Clone)]
struct ModuleData {
    file_path: PathBuf,
    source: String,
    tree: Tree,
    functions: BTreeSet<String>,
    methods: BTreeMap<String, BTreeSet<String>>,
    imports: BTreeMap<String, ImportTarget>,
}

impl ModuleData {
    fn from_parts(module: &str, file_path: PathBuf, source: String, tree: Tree) -> Result<Self> {
        let (functions, methods) = collect_symbols(&tree, &source);
        let imports = build_import_map(module, &source)?;
        Ok(Self {
            file_path,
            source,
            tree,
            functions,
            methods,
            imports,
        })
    }
}

#[derive(Clone, Debug)]
struct ImportTarget {
    module: String,
    symbol: Option<String>,
}

fn collect_symbols(
    tree: &Tree,
    source: &str,
) -> (BTreeSet<String>, BTreeMap<String, BTreeSet<String>>) {
    let mut functions = BTreeSet::new();
    let mut methods: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    let mut stack = vec![tree.root_node()];

    while let Some(node) = stack.pop() {
        match node.kind() {
            "function_definition" | "async_function_definition" => {
                if let Some(name_node) = node.child_by_field_name("name") {
                    if let Ok(text) = name_node.utf8_text(source.as_bytes()) {
                        functions.insert(text.to_string());
                    }
                }
            }
            "class_definition" => {
                if let Some(name_node) = node.child_by_field_name("name") {
                    if let Ok(class_name) = name_node.utf8_text(source.as_bytes()) {
                        let entry = methods.entry(class_name.to_string()).or_default();
                        if let Some(body_node) = node.child_by_field_name("body") {
                            collect_class_methods(body_node, source, entry);
                        }
                    }
                }
                continue;
            }
            _ => {}
        }

        for i in 0..node.child_count() {
            if let Some(child) = node.child(i) {
                stack.push(child);
            }
        }
    }

    (functions, methods)
}

fn collect_class_methods(node: Node<'_>, source: &str, entry: &mut BTreeSet<String>) {
    let mut stack = vec![node];
    while let Some(current) = stack.pop() {
        match current.kind() {
            "function_definition" | "async_function_definition" => {
                if let Some(name_node) = current.child_by_field_name("name") {
                    if let Ok(text) = name_node.utf8_text(source.as_bytes()) {
                        entry.insert(text.to_string());
                    }
                }
            }
            _ => {
                for i in 0..current.child_count() {
                    if let Some(child) = current.child(i) {
                        stack.push(child);
                    }
                }
            }
        }
    }
}

fn build_import_map(module: &str, source: &str) -> Result<BTreeMap<String, ImportTarget>> {
    let parsed = match parse(source, Mode::Module, module) {
        Ok(parsed) => parsed,
        Err(_) => return Ok(BTreeMap::new()),
    };
    let ast::Mod::Module(module_ast) = parsed else {
        return Ok(BTreeMap::new());
    };

    let mut map = BTreeMap::new();
    for stmt in module_ast.body {
        match stmt {
            Stmt::ImportFrom(import_from) => {
                let level = import_from.level.map(|lvl| lvl.to_u32());
                let base = resolve_import_base(
                    module,
                    import_from.module.as_ref().map(|id| id.as_str()),
                    level,
                );
                for alias in import_from.names {
                    if alias.name.as_str() == "*" {
                        continue;
                    }
                    let local = alias
                        .asname
                        .as_ref()
                        .map(|id| id.as_str())
                        .unwrap_or(alias.name.as_str());

                    let (target_module, target_symbol) =
                        split_import_target(&base, alias.name.as_str());
                    map.insert(
                        local.to_string(),
                        ImportTarget {
                            module: target_module,
                            symbol: target_symbol,
                        },
                    );
                }
            }
            Stmt::Import(import_stmt) => {
                for alias in import_stmt.names {
                    let local = alias
                        .asname
                        .as_ref()
                        .map(|id| id.as_str())
                        .unwrap_or(alias.name.as_str());
                    map.insert(
                        local.to_string(),
                        ImportTarget {
                            module: alias.name.as_str().to_string(),
                            symbol: None,
                        },
                    );
                }
            }
            _ => {}
        }
    }
    Ok(map)
}

fn split_import_target(base: &str, name: &str) -> (String, Option<String>) {
    if name.contains('.') {
        let mut parts: Vec<&str> = name.split('.').collect();
        let symbol = parts.pop().map(|s| s.to_string());
        let module = if base.is_empty() {
            parts.join(".")
        } else if parts.is_empty() {
            base.to_string()
        } else {
            format!("{base}.{}", parts.join("."))
        };
        (module, symbol)
    } else {
        let module = if base.is_empty() {
            String::new()
        } else {
            base.to_string()
        };
        (module, Some(name.to_string()))
    }
}

fn resolve_import_base(module: &str, target: Option<&str>, level: Option<u32>) -> String {
    let mut parts: Vec<&str> = module.split('.').collect();
    if let Some(lvl) = level {
        for _ in 0..lvl {
            parts.pop();
        }
    }
    if let Some(target) = target {
        if !target.is_empty() {
            parts.push(target);
        }
    }
    parts.join(".")
}

fn find_function_node<'a>(tree: &'a Tree, source: &str, symbol: &str) -> Option<Node<'a>> {
    let root = tree.root_node();
    let mut stack = vec![root];

    while let Some(node) = stack.pop() {
        if matches!(
            node.kind(),
            "function_definition" | "async_function_definition"
        ) {
            if let Some(name_node) = node.child_by_field_name("name") {
                if let Ok(text) = name_node.utf8_text(source.as_bytes()) {
                    if text == symbol {
                        return Some(node);
                    }
                }
            }
        }
        for i in 0..node.child_count() {
            if let Some(child) = node.child(i) {
                stack.push(child);
            }
        }
    }

    None
}

fn find_method_node<'a>(
    tree: &'a Tree,
    source: &str,
    class_name: &str,
    method_name: &str,
) -> Option<Node<'a>> {
    let root = tree.root_node();
    let mut stack = vec![root];

    while let Some(node) = stack.pop() {
        if node.kind() == "class_definition" {
            if let Some(name_node) = node.child_by_field_name("name") {
                if node_text_equals(name_node, source, class_name) {
                    if let Some(body_node) = node.child_by_field_name("body") {
                        let mut body_stack = vec![body_node];
                        while let Some(body_child) = body_stack.pop() {
                            if matches!(
                                body_child.kind(),
                                "function_definition" | "async_function_definition"
                            ) {
                                if let Some(name_node) = body_child.child_by_field_name("name") {
                                    if node_text_equals(name_node, source, method_name) {
                                        return Some(body_child);
                                    }
                                }
                            }
                            for i in 0..body_child.child_count() {
                                if let Some(child) = body_child.child(i) {
                                    body_stack.push(child);
                                }
                            }
                        }
                    }
                }
            }
            continue;
        }

        for i in 0..node.child_count() {
            if let Some(child) = node.child(i) {
                stack.push(child);
            }
        }
    }

    None
}

fn find_assignment_node<'a>(
    tree: &'a Tree,
    source: &str,
    symbol: &str,
    definition_span: Option<&SourceSpan>,
) -> Option<Node<'a>> {
    let root = tree.root_node();
    let mut stack = vec![root];

    while let Some(node) = stack.pop() {
        if node.kind() == "assignment" && assignment_targets_symbol(node, source, symbol) {
            if let Some(span) = definition_span {
                let start = node.start_position();
                if start.row as u32 != span.line.saturating_sub(1) {
                    stack.extend(children_of(node));
                    continue;
                }
            }
            return Some(node);
        }
        stack.extend(children_of(node));
    }

    None
}

fn assignment_targets_symbol(node: Node<'_>, source: &str, symbol: &str) -> bool {
    for i in 0..node.child_count() {
        if let Some(child) = node.child(i) {
            if child.kind() == "identifier" && node_text_equals(child, source, symbol) {
                return true;
            }
        }
    }
    false
}

fn children_of(node: Node<'_>) -> Vec<Node<'_>> {
    (0..node.child_count())
        .filter_map(|i| node.child(i))
        .collect()
}

fn node_text_equals(node: Node<'_>, source: &str, expected: &str) -> bool {
    node.utf8_text(source.as_bytes())
        .map(|text| text == expected)
        .unwrap_or(false)
}

fn span_from_node(node: Node<'_>, source: &str, file_path: &Path) -> SourceSpan {
    let start = node.start_byte();
    let (line, column) = source::line_col_at(source, start);
    SourceSpan {
        file: file_path.to_string_lossy().into_owned(),
        line,
        column,
    }
}

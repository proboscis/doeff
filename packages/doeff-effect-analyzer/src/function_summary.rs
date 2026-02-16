use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

use crate::{effect_registry::EffectRegistry, source, EffectUsage, SourceSpan};
use tree_sitter::Node;

#[derive(Debug, Clone, Default)]
pub struct FunctionSummary {
    pub symbol: String,
    pub local_effects: Vec<EffectUsage>,
    pub calls: Vec<CallEdge>,
    pub warnings: Vec<String>,
}

#[derive(Debug, Clone)]
pub enum ArgumentValue {
    Identifier(String),
    Attribute {
        object: Option<String>,
        attribute: String,
    },
    Lambda,
    Call(String),
    Other(String),
}

#[derive(Debug, Clone)]
pub struct CallArgument {
    pub name: Option<String>,
    pub value: ArgumentValue,
}

#[derive(Debug, Clone)]
pub struct CallEdge {
    pub label: String,
    pub span: SourceSpan,
    pub callee: Option<String>,
    pub object: Option<String>,
    pub extra_callees: Vec<String>,
    pub arguments: Vec<CallArgument>,
}

pub struct SummaryCollector<'a> {
    registry: &'a EffectRegistry,
    file_path: &'a Path,
    source_text: &'a str,
}

impl<'a> SummaryCollector<'a> {
    pub fn new(registry: &'a EffectRegistry, source_text: &'a str, file_path: &'a Path) -> Self {
        Self {
            registry,
            source_text,
            file_path,
        }
    }

    pub fn summarize_node(&self, node: Node<'_>) -> FunctionSummary {
        let mut summary = FunctionSummary::default();
        self.walk_node(node, &mut summary);
        summary
    }

    fn walk_node(&self, node: Node<'_>, summary: &mut FunctionSummary) {
        match node.kind() {
            "yield" | "yield_expression" | "yield_from_expression" => {
                if let Some(effect) = self.effect_from_node(node) {
                    summary.local_effects.push(effect);
                }
            }
            "call" => {
                let call = self.call_edge_from_node(node);
                summary.calls.push(call);
            }
            _ => {}
        }

        for i in 0..node.child_count() {
            if let Some(child) = node.child(i) {
                self.walk_node(child, summary);
            }
        }
    }

    fn effect_from_node(&self, node: Node<'_>) -> Option<EffectUsage> {
        let call_node = find_first_call(node)?;
        let call_text = call_node
            .utf8_text(self.source_text.as_bytes())
            .unwrap_or_default()
            .trim()
            .to_string();
        let key = match self.registry.classify_call(&call_text) {
            Some(key) => key,
            None => {
                return None;
            }
        };
        let start = call_node.start_byte();
        let (line, column) = source::line_col_at(self.source_text, start);
        let span = SourceSpan {
            file: self.file_path.to_string_lossy().into_owned(),
            line,
            column,
        };

        Some(EffectUsage {
            key,
            span: Some(span),
            via: None,
        })
    }

    fn call_edge_from_node(&self, node: Node<'_>) -> CallEdge {
        let text = self.node_text(node);
        let start = node.start_byte();
        let (line, column) = source::line_col_at(self.source_text, start);
        let span = SourceSpan {
            file: self.file_path.to_string_lossy().into_owned(),
            line,
            column,
        };

        let mut callee = None;
        let mut object = None;

        if let Some(function_node) = node.child_by_field_name("function") {
            match function_node.kind() {
                "identifier" => {
                    callee = Some(self.node_text(function_node));
                }
                "attribute" => {
                    let (obj_text, attr_text) = self.attribute_components(function_node);
                    callee = Some(attr_text);
                    object = obj_text;
                }
                _ => {
                    callee = Some(self.node_text(function_node));
                }
            }
        }

        if self.registry.classify_call(&text).is_some() {
            callee = None;
        }

        let arguments = node
            .child_by_field_name("arguments")
            .map(|args| self.arguments_from_node(args))
            .unwrap_or_default();

        let mut extra_set = BTreeSet::new();
        for arg in &arguments {
            match &arg.value {
                ArgumentValue::Identifier(name) => {
                    if callee.as_deref() != Some(name.as_str())
                        && object.as_deref() != Some(name.as_str())
                    {
                        extra_set.insert(name.clone());
                    }
                }
                ArgumentValue::Attribute {
                    object: obj,
                    attribute,
                } => {
                    if let Some(obj) = obj {
                        let combined = format!("{obj}.{attribute}");
                        extra_set.insert(combined);
                    } else {
                        extra_set.insert(attribute.clone());
                    }
                }
                _ => {}
            }
        }

        let extra_callees = extra_set.into_iter().collect();

        CallEdge {
            label: text,
            span,
            callee,
            object,
            extra_callees,
            arguments,
        }
    }

    fn node_text(&self, node: Node<'_>) -> String {
        node.utf8_text(self.source_text.as_bytes())
            .unwrap_or_default()
            .trim()
            .to_string()
    }

    fn arguments_from_node(&self, node: Node<'_>) -> Vec<CallArgument> {
        let mut arguments = Vec::new();
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                if child.kind() == "keyword_argument" {
                    let mut name = None;
                    let mut value_expr = None;
                    for j in 0..child.named_child_count() {
                        if let Some(inner) = child.named_child(j) {
                            if inner.kind() == "identifier" && name.is_none() {
                                name = inner
                                    .utf8_text(self.source_text.as_bytes())
                                    .ok()
                                    .map(|s| s.to_string());
                            } else if value_expr.is_none() {
                                value_expr = Some(inner);
                            }
                        }
                    }
                    if let Some(value_node) = value_expr {
                        arguments.push(CallArgument {
                            name,
                            value: self.argument_value_from_node(value_node),
                        });
                    }
                } else {
                    arguments.push(CallArgument {
                        name: None,
                        value: self.argument_value_from_node(child),
                    });
                }
            }
        }
        arguments
    }

    fn argument_value_from_node(&self, node: Node<'_>) -> ArgumentValue {
        match node.kind() {
            "identifier" => ArgumentValue::Identifier(self.node_text(node)),
            "attribute" => {
                let (object, attribute) = self.attribute_components(node);
                ArgumentValue::Attribute { object, attribute }
            }
            "lambda" => ArgumentValue::Lambda,
            "call" => {
                if let Some(function_node) = node.child_by_field_name("function") {
                    match function_node.kind() {
                        "identifier" => {
                            return ArgumentValue::Call(self.node_text(function_node));
                        }
                        "attribute" => {
                            let (object, attribute) = self.attribute_components(function_node);
                            let text = if let Some(obj) = object {
                                format!("{obj}.{attribute}")
                            } else {
                                attribute
                            };
                            return ArgumentValue::Call(text);
                        }
                        _ => {}
                    }
                }
                ArgumentValue::Call(self.node_text(node))
            }
            "string" | "integer" | "float" | "true" | "false" | "none" => {
                ArgumentValue::Other(self.node_text(node))
            }
            _ => ArgumentValue::Other(self.node_text(node)),
        }
    }

    fn attribute_components(&self, node: Node<'_>) -> (Option<String>, String) {
        let attribute = node
            .child_by_field_name("attribute")
            .and_then(|n| n.utf8_text(self.source_text.as_bytes()).ok())
            .map(|s| s.trim().to_string())
            .unwrap_or_else(|| self.node_text(node));

        let object = node
            .child_by_field_name("object")
            .or_else(|| node.child_by_field_name("value"))
            .and_then(|n| n.utf8_text(self.source_text.as_bytes()).ok())
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty());

        (object, attribute)
    }
}

#[derive(Debug, Default)]
pub struct CallGraph {
    summaries: BTreeMap<String, FunctionSummary>,
    edges: BTreeMap<String, BTreeSet<String>>,
}

impl CallGraph {
    pub fn insert(&mut self, name: String, summary: FunctionSummary) {
        self.summaries.insert(name.clone(), summary);
        self.edges.entry(name).or_default();
    }

    pub fn add_edge(&mut self, caller: &str, callee: &str) {
        self.edges
            .entry(caller.to_string())
            .or_default()
            .insert(callee.to_string());
    }

    pub fn summarize_effects(&self, root: &str) -> Vec<EffectUsage> {
        let mut visited = BTreeSet::new();
        let mut results = Vec::new();
        self.collect_effects(root, &mut visited, &mut results);
        results
    }

    fn collect_effects(
        &self,
        name: &str,
        visited: &mut BTreeSet<String>,
        results: &mut Vec<EffectUsage>,
    ) {
        if !visited.insert(name.to_string()) {
            return;
        }

        if let Some(summary) = self.summaries.get(name) {
            results.extend(summary.local_effects.clone());

            if let Some(children) = self.edges.get(name) {
                for child in children {
                    self.collect_effects(child, visited, results);
                }
            }
        }
    }
}

fn find_first_call(node: Node<'_>) -> Option<Node<'_>> {
    let mut stack = vec![node];
    while let Some(current) = stack.pop() {
        if current.kind() == "call" {
            return Some(current);
        }
        for i in 0..current.child_count() {
            if let Some(child) = current.child(i) {
                stack.push(child);
            }
        }
    }
    None
}

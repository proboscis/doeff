//! Graphviz DOT output for effect DAGs.

use crate::{EffectTreeNode, NodeKind, Report};

/// Render a Report as a DOT graph string.
/// If `effect_filter` is Some, only show nodes that use matching effects.
pub fn report_to_dot(report: &Report, effect_filter: Option<&str>) -> String {
    let mut dot = String::new();
    dot.push_str("digraph effects {\n");
    dot.push_str("  rankdir=TB;\n");
    dot.push_str("  node [fontname=\"Helvetica\", fontsize=10];\n");
    dot.push_str("  edge [fontname=\"Helvetica\", fontsize=8];\n\n");

    let mut counter = 0;
    emit_node(&report.tree.root, &mut dot, &mut counter, None, effect_filter);

    dot.push_str("}\n");
    dot
}

/// Render multiple HyFunctionInfos as a DAG in DOT format.
pub fn dag_to_dot(
    functions: &[(String, Vec<String>, Vec<String>)], // (name, effects, call_targets)
    effect_filter: Option<&str>,
) -> String {
    let mut dot = String::new();
    dot.push_str("digraph effects {\n");
    dot.push_str("  rankdir=TB;\n");
    dot.push_str("  node [fontname=\"Helvetica\", fontsize=10];\n");
    dot.push_str("  edge [fontname=\"Helvetica\", fontsize=8, color=\"#666666\"];\n");
    dot.push_str("  compound=true;\n\n");

    // Collect all unique effect names
    let mut all_effects = std::collections::BTreeSet::new();
    for (_, effects, _) in functions {
        for e in effects {
            all_effects.insert(e.clone());
        }
    }

    // Function nodes
    for (name, effects, _) in functions {
        let filtered_effects: Vec<&String> = if let Some(filter) = effect_filter {
            effects.iter().filter(|e| e.contains(filter)).collect()
        } else {
            effects.iter().collect()
        };

        if effect_filter.is_some() && filtered_effects.is_empty() {
            continue; // Skip functions with no matching effects
        }

        let effect_label = if filtered_effects.is_empty() {
            String::new()
        } else {
            format!(
                "\\n{}",
                filtered_effects
                    .iter()
                    .map(|e| e.as_str())
                    .collect::<Vec<_>>()
                    .join("\\n")
            )
        };

        dot.push_str(&format!(
            "  \"{}\" [shape=box, style=filled, fillcolor=\"#e8f0fe\", label=\"{}{}\"];\n",
            name, name, effect_label
        ));
    }

    // Effect nodes (diamonds)
    for effect in &all_effects {
        if let Some(filter) = effect_filter {
            if !effect.contains(filter) {
                continue;
            }
        }
        dot.push_str(&format!(
            "  \"effect:{}\" [shape=diamond, style=filled, fillcolor=\"#fce8e6\", label=\"{}\"];\n",
            effect, effect
        ));
    }

    // Edges: function → called function
    for (name, _, calls) in functions {
        for target in calls {
            // Check if target exists as a function node
            let target_exists = functions.iter().any(|(n, _, _)| n == target);
            if target_exists {
                dot.push_str(&format!(
                    "  \"{}\" -> \"{}\" [style=solid];\n",
                    name, target
                ));
            } else {
                // External/unresolved call — dashed
                dot.push_str(&format!(
                    "  \"ext:{}\" [shape=box, style=\"dashed,filled\", fillcolor=\"#f5f5f5\", label=\"{}\"];\n",
                    target, target
                ));
                dot.push_str(&format!(
                    "  \"{}\" -> \"ext:{}\" [style=dashed];\n",
                    name, target
                ));
            }
        }
    }

    // Edges: function → effect
    for (name, effects, _) in functions {
        for effect in effects {
            if let Some(filter) = effect_filter {
                if !effect.contains(filter) {
                    continue;
                }
            }
            dot.push_str(&format!(
                "  \"{}\" -> \"effect:{}\" [style=dotted, color=\"#cc0000\"];\n",
                name, effect
            ));
        }
    }

    dot.push_str("}\n");
    dot
}

fn emit_node(
    node: &EffectTreeNode,
    dot: &mut String,
    counter: &mut usize,
    parent_id: Option<usize>,
    effect_filter: Option<&str>,
) {
    let id = *counter;
    *counter += 1;

    // Apply filter
    if let Some(filter) = effect_filter {
        if !node_contains_effect(node, filter) {
            return;
        }
    }

    let (shape, color) = match node.kind {
        NodeKind::Root => ("box", "#f0f0f0"),
        NodeKind::Function => ("box", "#e8f0fe"),
        NodeKind::Effect => ("diamond", "#fce8e6"),
        NodeKind::Unresolved => ("box", "#fff3e0"),
    };

    let label = node.label.replace('"', "\\\"");
    dot.push_str(&format!(
        "  n{} [shape={}, style=filled, fillcolor=\"{}\", label=\"{}\"];\n",
        id, shape, color, label
    ));

    if let Some(pid) = parent_id {
        dot.push_str(&format!("  n{} -> n{};\n", pid, id));
    }

    for child in &node.children {
        emit_node(child, dot, counter, Some(id), effect_filter);
    }
}

fn node_contains_effect(node: &EffectTreeNode, filter: &str) -> bool {
    if node.effects.iter().any(|e| e.contains(filter)) {
        return true;
    }
    node.children.iter().any(|c| node_contains_effect(c, filter))
}

//! Interactive HTML tree visualization for effect traces.
//!
//! Generates a self-contained HTML file with a collapsible tree view
//! where each function call is an internal node and each effect yield
//! is a leaf — like a stack trace tree.

use crate::hy_analyzer::HyModuleInfo;
use crate::function_summary::FunctionSummary;
use serde::Serialize;
use std::collections::BTreeSet;

/// A node in the effect trace tree.
#[derive(Debug, Clone, Serialize)]
pub struct TraceNode {
    /// Display label
    pub label: String,
    /// Node type: "function", "effect", "external"
    pub kind: String,
    /// Source file path (if known)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub file: Option<String>,
    /// Line number (if known)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub line: Option<u32>,
    /// Effect key (for effect nodes)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub effect_key: Option<String>,
    /// Children (calls and effects in source order)
    pub children: Vec<TraceNode>,
}

/// Build an ordered trace tree from a root function.
/// Effects and calls appear in the order they occur in the source.
pub fn build_trace_tree(
    root_name: &str,
    all_infos: &[(std::path::PathBuf, HyModuleInfo)],
    effect_filter: Option<&str>,
) -> TraceNode {
    let mut visited = BTreeSet::new();
    build_node(root_name, all_infos, effect_filter, &mut visited)
}

fn build_node(
    name: &str,
    all_infos: &[(std::path::PathBuf, HyModuleInfo)],
    effect_filter: Option<&str>,
    visited: &mut BTreeSet<String>,
) -> TraceNode {
    // Find function definition across all files
    let func = all_infos.iter().find_map(|(path, info)| {
        info.function_defs
            .get(name)
            .map(|f| (path.clone(), f.clone()))
    });

    let Some((file_path, func_info)) = func else {
        return TraceNode {
            label: name.to_string(),
            kind: "external".to_string(),
            file: None,
            line: None,
            effect_key: None,
            children: Vec::new(),
        };
    };

    if !visited.insert(name.to_string()) {
        return TraceNode {
            label: format!("{} (recursive)", name),
            kind: "function".to_string(),
            file: Some(file_path.to_string_lossy().to_string()),
            line: Some(func_info.span.line),
            effect_key: None,
            children: Vec::new(),
        };
    }

    let summary = &func_info.summary;

    // Build children in source order by interleaving effects and calls
    // based on their source positions
    let mut children = build_ordered_children(
        summary,
        &file_path,
        all_infos,
        effect_filter,
        visited,
    );

    // Apply effect filter: if filter is set, prune branches without matching effects
    if let Some(filter) = effect_filter {
        children.retain(|child| node_matches_filter(child, filter));
    }

    visited.remove(name);

    TraceNode {
        label: name.to_string(),
        kind: "function".to_string(),
        file: Some(file_path.to_string_lossy().to_string()),
        line: Some(func_info.span.line),
        effect_key: None,
        children,
    }
}

fn build_ordered_children(
    summary: &FunctionSummary,
    file_path: &std::path::Path,
    all_infos: &[(std::path::PathBuf, HyModuleInfo)],
    effect_filter: Option<&str>,
    visited: &mut BTreeSet<String>,
) -> Vec<TraceNode> {
    // Collect all items (effects + calls) with their source line for ordering
    #[derive(Debug)]
    enum Item {
        Effect { key: String, line: u32 },
        Call { callee: String, line: u32 },
    }

    let mut items = Vec::new();

    for effect in &summary.local_effects {
        let line = effect.span.as_ref().map(|s| s.line).unwrap_or(0);
        items.push(Item::Effect {
            key: effect.key.clone(),
            line,
        });
    }

    for call in &summary.calls {
        let line = call.span.line;
        if let Some(callee) = &call.callee {
            items.push(Item::Call {
                callee: callee.clone(),
                line,
            });
        }
    }

    // Sort by source line
    items.sort_by_key(|item| match item {
        Item::Effect { line, .. } => *line,
        Item::Call { line, .. } => *line,
    });

    let file_str = file_path.to_string_lossy().to_string();

    items
        .into_iter()
        .map(|item| match item {
            Item::Effect { key, line } => TraceNode {
                label: key.clone(),
                kind: "effect".to_string(),
                file: Some(file_str.clone()),
                line: Some(line),
                effect_key: Some(key),
                children: Vec::new(),
            },
            Item::Call { callee, line } => {
                let mut node = build_node(&callee, all_infos, effect_filter, visited);
                // Override line to the call site, not the definition site
                if node.kind == "external" {
                    node.line = Some(line);
                    node.file = Some(file_str.clone());
                }
                node
            }
        })
        .collect()
}

fn node_matches_filter(node: &TraceNode, filter: &str) -> bool {
    if let Some(key) = &node.effect_key {
        if key.contains(filter) {
            return true;
        }
    }
    node.children.iter().any(|c| node_matches_filter(c, filter))
}

/// Generate a self-contained HTML file with interactive tree visualization.
pub fn render_html(tree: &TraceNode, title: &str) -> String {
    let tree_json = serde_json::to_string(tree).unwrap_or_else(|_| "{}".to_string());

    format!(
        r##"<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>SEDA — {title}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: 'SF Mono', 'Fira Code', 'JetBrains Mono', monospace;
    font-size: 13px;
    background: #1a1a2e;
    color: #e0e0e0;
    padding: 20px;
  }}
  h1 {{
    font-size: 16px;
    color: #7c83ff;
    margin-bottom: 8px;
    font-weight: 500;
  }}
  .toolbar {{
    margin-bottom: 16px;
    display: flex;
    gap: 12px;
    align-items: center;
  }}
  .toolbar button {{
    background: #2a2a4a;
    color: #b0b0d0;
    border: 1px solid #3a3a5a;
    padding: 4px 12px;
    border-radius: 4px;
    cursor: pointer;
    font-family: inherit;
    font-size: 12px;
  }}
  .toolbar button:hover {{ background: #3a3a6a; color: #fff; }}
  .toolbar input {{
    background: #2a2a4a;
    color: #e0e0e0;
    border: 1px solid #3a3a5a;
    padding: 4px 8px;
    border-radius: 4px;
    font-family: inherit;
    font-size: 12px;
    width: 200px;
  }}
  .tree {{ padding-left: 0; }}
  .tree ul {{ padding-left: 20px; list-style: none; }}
  .tree li {{ position: relative; padding: 2px 0; }}
  .tree li::before {{
    content: '';
    position: absolute;
    left: -14px;
    top: 0;
    bottom: 0;
    width: 1px;
    background: #3a3a5a;
  }}
  .tree li:last-child::before {{ height: 14px; }}
  .tree li::after {{
    content: '';
    position: absolute;
    left: -14px;
    top: 12px;
    width: 12px;
    height: 1px;
    background: #3a3a5a;
  }}
  .tree > ul > li::before,
  .tree > ul > li::after {{ display: none; }}
  .node {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 2px 6px;
    border-radius: 3px;
    cursor: default;
    line-height: 1.6;
  }}
  .node:hover {{ background: #2a2a4a; }}
  .node.function {{ color: #82aaff; }}
  .node.effect {{ color: #f78c6c; font-weight: 600; }}
  .node.external {{ color: #676e95; font-style: italic; }}
  .node.function.has-children {{ cursor: pointer; }}
  .toggle {{
    display: inline-block;
    width: 16px;
    text-align: center;
    color: #5a5a8a;
    font-size: 10px;
    user-select: none;
  }}
  .badge {{
    font-size: 10px;
    padding: 0 5px;
    border-radius: 8px;
    color: #1a1a2e;
    font-weight: 600;
  }}
  .badge.fn {{ background: #4a5568; color: #a0aec0; }}
  .badge.fx {{ background: #c53030; color: #fff; }}
  .badge.ext {{ background: #2d3748; color: #718096; }}
  .loc {{
    font-size: 10px;
    color: #4a4a6a;
    margin-left: 4px;
  }}
  .hidden {{ display: none; }}
  .collapsed > ul {{ display: none; }}
  .match {{ background: #44337a; border-radius: 2px; }}
  .stats {{
    color: #5a5a8a;
    font-size: 11px;
    margin-bottom: 12px;
  }}
</style>
</head>
<body>

<h1>SEDA — {title}</h1>
<div class="toolbar">
  <input type="text" id="filter" placeholder="Filter effects..." oninput="filterTree()">
  <button onclick="expandAll()">Expand All</button>
  <button onclick="collapseAll()">Collapse All</button>
  <button onclick="collapseDepth(2)">Depth 2</button>
  <button onclick="collapseDepth(3)">Depth 3</button>
</div>
<div class="stats" id="stats"></div>
<div class="tree" id="tree"></div>

<script>
const DATA = {tree_json};

function icon(kind) {{
  if (kind === 'effect') return '◆';
  if (kind === 'external') return '○';
  return '▸';
}}

function badgeClass(kind) {{
  if (kind === 'effect') return 'fx';
  if (kind === 'external') return 'ext';
  return 'fn';
}}

function badgeLabel(kind) {{
  if (kind === 'effect') return 'effect';
  if (kind === 'external') return 'ext';
  return 'fn';
}}

function countEffects(node) {{
  let n = 0;
  if (node.kind === 'effect') n = 1;
  if (node.children) node.children.forEach(c => n += countEffects(c));
  return n;
}}

function countFunctions(node) {{
  let n = 0;
  if (node.kind === 'function') n = 1;
  if (node.children) node.children.forEach(c => n += countFunctions(c));
  return n;
}}

function renderNode(node, depth) {{
  const hasChildren = node.children && node.children.length > 0;
  const li = document.createElement('li');
  if (hasChildren) li.className = depth > 1 ? 'collapsed' : '';

  const span = document.createElement('span');
  span.className = 'node ' + node.kind + (hasChildren ? ' has-children' : '');

  const toggle = document.createElement('span');
  toggle.className = 'toggle';
  toggle.textContent = hasChildren ? (depth > 1 ? '▶' : '▼') : ' ';
  span.appendChild(toggle);

  const badge = document.createElement('span');
  badge.className = 'badge ' + badgeClass(node.kind);
  badge.textContent = badgeLabel(node.kind);
  span.appendChild(badge);

  const label = document.createElement('span');
  label.textContent = ' ' + node.label;
  label.className = 'label';
  span.appendChild(label);

  if (node.file && node.line) {{
    const loc = document.createElement('span');
    const shortFile = node.file.split('/').slice(-2).join('/');
    loc.className = 'loc';
    loc.textContent = shortFile + ':' + node.line;
    span.appendChild(loc);
  }}

  if (hasChildren) {{
    span.onclick = function(e) {{
      e.stopPropagation();
      li.classList.toggle('collapsed');
      toggle.textContent = li.classList.contains('collapsed') ? '▶' : '▼';
    }};
  }}

  li.appendChild(span);

  if (hasChildren) {{
    const ul = document.createElement('ul');
    node.children.forEach(child => {{
      ul.appendChild(renderNode(child, depth + 1));
    }});
    li.appendChild(ul);
  }}

  return li;
}}

function renderTree() {{
  const container = document.getElementById('tree');
  container.innerHTML = '';
  const ul = document.createElement('ul');
  ul.appendChild(renderNode(DATA, 0));
  container.appendChild(ul);

  const nEffects = countEffects(DATA);
  const nFunctions = countFunctions(DATA);
  document.getElementById('stats').textContent =
    nFunctions + ' functions, ' + nEffects + ' effect yields';
}}

function expandAll() {{
  document.querySelectorAll('.tree li.collapsed').forEach(li => {{
    li.classList.remove('collapsed');
    li.querySelector(':scope > .node .toggle').textContent = '▼';
  }});
}}

function collapseAll() {{
  document.querySelectorAll('.tree li').forEach(li => {{
    if (li.querySelector(':scope > ul')) {{
      li.classList.add('collapsed');
      const t = li.querySelector(':scope > .node .toggle');
      if (t) t.textContent = '▶';
    }}
  }});
}}

function collapseDepth(maxDepth) {{
  function walk(el, depth) {{
    if (el.tagName === 'LI' && el.querySelector(':scope > ul')) {{
      const t = el.querySelector(':scope > .node .toggle');
      if (depth >= maxDepth) {{
        el.classList.add('collapsed');
        if (t) t.textContent = '▶';
      }} else {{
        el.classList.remove('collapsed');
        if (t) t.textContent = '▼';
      }}
    }}
    el.querySelectorAll(':scope > ul > li').forEach(child => walk(child, depth + 1));
  }}
  document.querySelectorAll('.tree > ul > li').forEach(li => walk(li, 0));
}}

function filterTree() {{
  const query = document.getElementById('filter').value.toLowerCase();
  document.querySelectorAll('.tree .node').forEach(span => {{
    span.classList.remove('match');
    const label = span.querySelector('.label');
    if (query && label && label.textContent.toLowerCase().includes(query)) {{
      span.classList.add('match');
      // Expand parents
      let el = span.closest('li');
      while (el) {{
        el.classList.remove('collapsed');
        const t = el.querySelector(':scope > .node .toggle');
        if (t) t.textContent = '▼';
        el = el.parentElement?.closest('li');
      }}
    }}
  }});
}}

renderTree();
</script>
</body>
</html>"##,
        title = title,
        tree_json = tree_json
    )
}

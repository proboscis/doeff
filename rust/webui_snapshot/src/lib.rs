use indexmap::IndexMap;
use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::types::{PyAny, PyDict, PyFrozenSet, PyIterator, PyList, PyModule, PySet, PyTuple};
use std::collections::HashSet;

const DEFAULT_IMAGE_WIDTH: f64 = 240.0;
const DEFAULT_IMAGE_HEIGHT: f64 = 180.0;

fn escape_html(input: &str) -> String {
    let mut escaped = String::with_capacity(input.len());
    for ch in input.chars() {
        match ch {
            '&' => escaped.push_str("&amp;"),
            '<' => escaped.push_str("&lt;"),
            '>' => escaped.push_str("&gt;"),
            '"' => escaped.push_str("&quot;"),
            '\'' => escaped.push_str("&#39;"),
            _ => escaped.push(ch),
        }
    }
    escaped
}

#[derive(Debug)]
struct NodePayload {
    label: String,
    value_repr: String,
    meta: Py<PyAny>,
    image: Option<String>,
    value_image: Option<String>,
    meta_images: Vec<String>,
    display_width: Option<f64>,
    display_height: Option<f64>,
}

fn extract_node_payload(
    py: Python<'_>,
    payload_obj: Py<PyAny>,
    deepcopy: &PyAny,
) -> PyResult<NodePayload> {
    let payload = payload_obj.as_ref(py);
    let label: String = payload.getattr("label")?.extract()?;
    let value_repr: String = payload.getattr("value_repr")?.extract()?;

    let meta_obj = payload.getattr("meta")?;
    let meta_copy = deepcopy.call1((meta_obj,))?;
    let meta: Py<PyAny> = meta_copy.into();

    let meta_images_obj = payload.getattr("meta_images")?;
    let meta_images: Vec<String> = meta_images_obj.extract()?;

    let image: Option<String> = payload.getattr("image")?.extract()?;
    let value_image: Option<String> = payload.getattr("value_image")?.extract()?;
    let display_width: Option<f64> = payload.getattr("display_width")?.extract()?;
    let display_height: Option<f64> = payload.getattr("display_height")?.extract()?;

    Ok(NodePayload {
        label,
        value_repr,
        meta,
        image,
        value_image,
        meta_images,
        display_width,
        display_height,
    })
}

fn ensure_node(
    py: Python<'_>,
    node: &PyAny,
    payload: &NodePayload,
    node_ids: &mut IndexMap<usize, String>,
    nodes_payload: &mut IndexMap<String, Py<PyAny>>,
    node_counter: &mut usize,
) -> PyResult<String> {
    let node_key = node.as_ptr() as usize;
    let node_id = if let Some(existing) = node_ids.get(&node_key) {
        existing.clone()
    } else {
        let assigned = format!("node-{}", *node_counter);
        *node_counter += 1;
        node_ids.insert(node_key, assigned.clone());
        assigned
    };

    if let Some(entry) = nodes_payload.get(&node_id) {
        let entry_dict = entry.as_ref(py);
        let data: &PyDict = entry_dict.get_item("data")?.downcast()?;

        // Merge metadata
        let payload_meta_any = payload.meta.as_ref(py);
        let payload_meta_dict: &PyDict = payload_meta_any.downcast()?;
        if !payload_meta_dict.is_empty() {
            let payload_meta_mapping = payload_meta_dict.as_mapping();
            if let Some(existing_meta_any) = data.get_item("meta")? {
                let existing_meta: &PyDict = existing_meta_any.downcast()?;
                existing_meta.update(payload_meta_mapping)?;
            } else {
                data.set_item("meta", payload_meta_dict)?;
            }
        }

        // Update meta_images
        if !payload.meta_images.is_empty() {
            let images_list = match data.get_item("meta_images")? {
                Some(obj) => obj.downcast::<PyList>()?,
                None => {
                    let new_list = PyList::empty(py);
                    data.set_item("meta_images", new_list)?;
                    new_list
                }
            };
            for candidate in &payload.meta_images {
                let exists = images_list
                    .iter()
                    .any(|item| match item.extract::<String>() {
                        Ok(existing) => existing == *candidate,
                        Err(_) => false,
                    });
                if !exists {
                    images_list.append(candidate)?;
                }
            }
        }

        // Determine image source
        let mut image_source = payload.image.clone();
        if image_source.is_none() && !payload.meta_images.is_empty() {
            image_source = Some(payload.meta_images[0].clone());
        }
        if image_source.is_none() {
            image_source = data
                .get_item("image")
                .ok()
                .and_then(|opt| opt.and_then(|obj| obj.extract::<String>().ok()));
        }

        if let Some(image_src) = image_source {
            data.set_item("image", image_src)?;
            let width = payload
                .display_width
                .or_else(|| {
                    data.get_item("image_width")
                        .ok()
                        .and_then(|opt| opt.and_then(|obj| obj.extract().ok()))
                })
                .unwrap_or(DEFAULT_IMAGE_WIDTH);
            let height = payload
                .display_height
                .or_else(|| {
                    data.get_item("image_height")
                        .ok()
                        .and_then(|opt| opt.and_then(|obj| obj.extract().ok()))
                })
                .unwrap_or(DEFAULT_IMAGE_HEIGHT);
            data.set_item("image_width", width)?;
            data.set_item("image_height", height)?;
        }

        if let Some(value_image) = &payload.value_image {
            if data.get_item("value_image")?.is_none() {
                data.set_item("value_image", value_image)?;
            }
        }

        Ok(node_id)
    } else {
        let data = PyDict::new(py);
        data.set_item("id", &node_id)?;
        data.set_item("label", &payload.label)?;
        data.set_item("value_repr", &payload.value_repr)?;
        let payload_meta_any = payload.meta.as_ref(py);
        let payload_meta_dict: &PyDict = payload_meta_any.downcast()?;
        if !payload_meta_dict.is_empty() {
            data.set_item("meta", payload_meta_dict)?;
        }
        if !payload.meta_images.is_empty() {
            let images = PyList::new(py, &payload.meta_images);
            data.set_item("meta_images", images)?;
        }
        if let Some(image_src) = payload.image.as_ref() {
            data.set_item("image", image_src)?;
            let width = payload.display_width.unwrap_or(DEFAULT_IMAGE_WIDTH);
            let height = payload.display_height.unwrap_or(DEFAULT_IMAGE_HEIGHT);
            data.set_item("image_width", width)?;
            data.set_item("image_height", height)?;
        }
        if let Some(value_image) = payload.value_image.as_ref() {
            data.set_item("value_image", value_image)?;
        }

        let node_entry = PyDict::new(py);
        node_entry.set_item("data", data)?;
        nodes_payload.insert(node_id.clone(), node_entry.into_py(py));
        Ok(node_id)
    }
}

#[allow(clippy::too_many_arguments)]
#[pyfunction]
#[pyo3(signature = (
    graph,
    baseline_snapshot=None,
    *,
    node_ids=None,
    edge_ids=None,
    node_counter=1usize,
    edge_counter=1usize,
    last_node_id=None,
    mark_success=false,
    merge=false,
    payload_getter=None,
    steps_override=None,
))]
fn build_snapshot(
    py: Python<'_>,
    graph: &PyAny,
    baseline_snapshot: Option<&PyAny>,
    node_ids: Option<&PyDict>,
    edge_ids: Option<&PyDict>,
    mut node_counter: usize,
    mut edge_counter: usize,
    last_node_id: Option<&PyAny>,
    mark_success: bool,
    merge: bool,
    payload_getter: Option<&PyAny>,
    steps_override: Option<&PyAny>,
) -> PyResult<PyObject> {
    let payload_getter =
        payload_getter.ok_or_else(|| PyRuntimeError::new_err("payload_getter required"))?;

    let mut node_ids_map: IndexMap<usize, String> = IndexMap::new();
    if let Some(mapping) = node_ids {
        for (key, value) in mapping.iter() {
            let key_val: usize = key.extract()?;
            let node_id: String = value.extract()?;
            node_ids_map.insert(key_val, node_id);
        }
    }

    let mut edge_ids_map: IndexMap<(usize, usize), String> = IndexMap::new();
    if let Some(mapping) = edge_ids {
        for (key, value) in mapping.iter() {
            let tuple = key.downcast::<PyTuple>()?;
            if tuple.len() != 2 {
                continue;
            }
            let source: usize = tuple.get_item(0)?.extract()?;
            let target: usize = tuple.get_item(1)?.extract()?;
            let edge_id: String = value.extract()?;
            edge_ids_map.insert((source, target), edge_id);
        }
    }

    let mut nodes_payload: IndexMap<String, Py<PyAny>> = IndexMap::new();
    let mut edges_payload: IndexMap<String, Py<PyAny>> = IndexMap::new();

    if let Some(snapshot) = baseline_snapshot {
        if let Ok(nodes) = snapshot.getattr("nodes") {
            for node in nodes.downcast::<PyList>()?.iter() {
                let node_dict = node.downcast::<PyDict>()?;
                let data_any = node_dict
                    .get_item("data")?
                    .ok_or_else(|| PyRuntimeError::new_err("node entry missing data"))?;
                let data = data_any.downcast::<PyDict>()?;
                let node_id_any = data
                    .get_item("id")?
                    .ok_or_else(|| PyRuntimeError::new_err("node data missing id"))?;
                let node_id: String = node_id_any.extract()?;

                let data_copy = PyDict::new(py);
                for (k, v) in data.iter() {
                    data_copy.set_item(k, v)?;
                }
                let node_copy = PyDict::new(py);
                node_copy.set_item("data", data_copy)?;
                for (k, v) in node_dict.iter() {
                    if let Ok(key_str) = k.extract::<&str>() {
                        if key_str == "data" {
                            continue;
                        }
                    }
                    node_copy.set_item(k, v)?;
                }

                nodes_payload.insert(node_id, node_copy.into_py(py));
            }
        }

        if let Ok(edges) = snapshot.getattr("edges") {
            for edge in edges.downcast::<PyList>()?.iter() {
                let edge_dict = edge.downcast::<PyDict>()?;
                let data_any = edge_dict
                    .get_item("data")?
                    .ok_or_else(|| PyRuntimeError::new_err("edge entry missing data"))?;
                let data = data_any.downcast::<PyDict>()?;
                let edge_id_any = data
                    .get_item("id")?
                    .ok_or_else(|| PyRuntimeError::new_err("edge data missing id"))?;
                let edge_id: String = edge_id_any.extract()?;

                let data_copy = PyDict::new(py);
                for (k, v) in data.iter() {
                    data_copy.set_item(k, v)?;
                }
                let edge_copy = PyDict::new(py);
                edge_copy.set_item("data", data_copy)?;
                for (k, v) in edge_dict.iter() {
                    if let Ok(key_str) = k.extract::<&str>() {
                        if key_str == "data" {
                            continue;
                        }
                    }
                    edge_copy.set_item(k, v)?;
                }

                edges_payload.insert(edge_id, edge_copy.into_py(py));
            }
        }
    }

    let deepcopy_mod = PyModule::import(py, "copy")?;
    let deepcopy = deepcopy_mod.getattr("deepcopy")?;

    let mut steps: Vec<Py<PyAny>> = Vec::new();
    let mut seen_step_ids: HashSet<usize> = HashSet::new();

    if let Some(override_obj) = steps_override {
        let steps_iter = PyIterator::from_object(override_obj)?;
        for item in steps_iter {
            let step = item?.to_object(py);
            let ptr = step.as_ptr() as usize;
            if seen_step_ids.insert(ptr) {
                steps.push(step);
            }
        }
    } else {
        let steps_attr = graph.getattr("steps")?;
        if let Ok(frozen) = steps_attr.downcast::<PyFrozenSet>() {
            for item in frozen.iter() {
                let step = item.to_object(py);
                let ptr = step.as_ptr() as usize;
                if seen_step_ids.insert(ptr) {
                    steps.push(step);
                }
            }
        } else if let Ok(pyset) = steps_attr.downcast::<PySet>() {
            for item in pyset.iter() {
                let step = item.to_object(py);
                let ptr = step.as_ptr() as usize;
                if seen_step_ids.insert(ptr) {
                    steps.push(step);
                }
            }
        } else {
            let steps_iter = PyIterator::from_object(steps_attr)?;
            for item in steps_iter {
                let step = item?.to_object(py);
                let ptr = step.as_ptr() as usize;
                if seen_step_ids.insert(ptr) {
                    steps.push(step);
                }
            }
        }
    }

    let last_step = graph.getattr("last")?.to_object(py);
    let last_ptr = last_step.as_ptr() as usize;
    if seen_step_ids.insert(last_ptr) {
        steps.push(last_step.clone_ref(py));
    }
    let last_step_ref = last_step.as_ref(py);

    let mut seen_nodes: HashSet<usize> = HashSet::new();
    let mut final_node_id: Option<String> = None;

    for step_obj in &steps {
        let step = step_obj.as_ref(py);

        let inputs = step.getattr("inputs")?.downcast::<PyTuple>()?;
        let mut sources: Vec<(String, usize)> = Vec::with_capacity(inputs.len());
        for input_node in inputs.iter() {
            let payload_obj = payload_getter.call1((input_node, py.None()))?;
            let input_payload = extract_node_payload(py, payload_obj.into_py(py), deepcopy)?;
            let source_id = ensure_node(
                py,
                input_node,
                &input_payload,
                &mut node_ids_map,
                &mut nodes_payload,
                &mut node_counter,
            )?;
            let source_key = input_node.as_ptr() as usize;
            seen_nodes.insert(source_key);
            sources.push((source_id, source_key));
        }

        let output_node = step.getattr("output")?;
        let meta = step.getattr("meta")?;
        let payload_obj = payload_getter.call1((output_node, meta))?;
        let payload = extract_node_payload(py, payload_obj.into_py(py), deepcopy)?;
        let target_id = ensure_node(
            py,
            output_node,
            &payload,
            &mut node_ids_map,
            &mut nodes_payload,
            &mut node_counter,
        )?;
        let output_key = output_node.as_ptr() as usize;
        seen_nodes.insert(output_key);

        if step.is(last_step_ref) {
            final_node_id = Some(target_id.clone());
        }

        for (source_id, source_key) in sources {
            let edge_key = (source_key, output_key);
            let edge_id = if let Some(existing) = edge_ids_map.get(&edge_key) {
                existing.clone()
            } else {
                let assigned = format!("edge-{}", edge_counter);
                edge_counter += 1;
                edge_ids_map.insert(edge_key, assigned.clone());
                assigned
            };

            if !edges_payload.contains_key(&edge_id) {
                let data = PyDict::new(py);
                data.set_item("id", &edge_id)?;
                data.set_item("source", &source_id)?;
                data.set_item("target", &target_id)?;
                let edge_entry = PyDict::new(py);
                edge_entry.set_item("data", data)?;
                edges_payload.insert(edge_id, edge_entry.into_py(py));
            }
        }
    }

    if !merge {
        let unused_nodes: Vec<usize> = node_ids_map
            .keys()
            .copied()
            .filter(|key| !seen_nodes.contains(key))
            .collect();
        for key in unused_nodes {
            if let Some(node_id) = node_ids_map.swap_remove(&key) {
                nodes_payload.swap_remove(&node_id);
            }
        }

        let unused_edges: Vec<(usize, usize)> = edge_ids_map
            .keys()
            .copied()
            .filter(|(source, target)| !seen_nodes.contains(source) || !seen_nodes.contains(target))
            .collect();
        for key in unused_edges {
            if let Some(edge_id) = edge_ids_map.swap_remove(&key) {
                edges_payload.swap_remove(&edge_id);
            }
        }
    }

    if final_node_id.is_none() {
        let last_output = last_step_ref.getattr("output")?;
        let last_key = last_output.as_ptr() as usize;
        if let Some(node_id) = node_ids_map.get(&last_key) {
            final_node_id = Some(node_id.clone());
        }
    }

    if let Some(ref final_id) = final_node_id {
        if let Some(node_entry) = nodes_payload.get(final_id) {
            let entry_dict = node_entry.as_ref(py);
            let data: &PyDict = entry_dict.get_item("data")?.downcast()?;
            data.set_item("is_last", true)?;
            if mark_success {
                data.set_item("is_success", true)?;
            }
        }
    }

    let node_entries: Vec<PyObject> = nodes_payload
        .values()
        .map(|value| value.to_object(py))
        .collect();
    let edge_entries: Vec<PyObject> = edges_payload
        .values()
        .map(|value| value.to_object(py))
        .collect();

    let result = PyDict::new(py);
    result.set_item("nodes", PyList::new(py, node_entries))?;
    result.set_item("edges", PyList::new(py, edge_entries))?;

    let node_ids_dict = PyDict::new(py);
    for (key, value) in node_ids_map.iter() {
        node_ids_dict.set_item(key, value)?;
    }
    result.set_item("node_ids", node_ids_dict)?;

    let edge_ids_dict = PyDict::new(py);
    for ((source, target), edge_id) in edge_ids_map.iter() {
        let key = PyTuple::new(py, vec![source.to_object(py), target.to_object(py)]);
        edge_ids_dict.set_item(key, edge_id)?;
    }
    result.set_item("edge_ids", edge_ids_dict)?;

    let mut last_node_id_val: Option<String> = match last_node_id {
        Some(value) if !value.is_none() => value.extract().ok(),
        _ => None,
    };
    if final_node_id.is_some() {
        last_node_id_val = final_node_id.clone();
    }

    match last_node_id_val {
        Some(ref node_id) => result.set_item("last_node_id", node_id)?,
        None => result.set_item("last_node_id", py.None())?,
    }

    match final_node_id {
        Some(ref node_id) => result.set_item("final_node_id", node_id)?,
        None => result.set_item("final_node_id", py.None())?,
    }

    result.set_item("node_counter", node_counter)?;
    result.set_item("edge_counter", edge_counter)?;

    Ok(result.to_object(py))
}

#[pyfunction(name = "build_snapshot_html")]
#[pyo3(signature = (snapshot, title="doeff Graph Snapshot"))]
fn build_snapshot_html_v2(snapshot: &PyAny, title: &str) -> PyResult<String> {
    let py = snapshot.py();
    let snapshot_dict: &PyDict = snapshot.downcast()?;
    let title_html = escape_html(title);
    let json_mod = PyModule::import(py, "json")?;
    let dumps = json_mod.getattr("dumps")?;
    let snapshot_json: String = dumps.call1((snapshot_dict,))?.extract()?;
    let mut snapshot_json_safe = snapshot_json.replace("\\", "\\\\");
    snapshot_json_safe = snapshot_json_safe.replace("'", "\\'");
    snapshot_json_safe = snapshot_json_safe.replace("</", "<\\/");

    let template = r#"<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>__TITLE__</title>
  <link rel="preconnect" href="https://cdnjs.cloudflare.com" crossorigin>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.26.0/cytoscape.min.js"></script>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/dagre/0.8.5/dagre.min.js"></script>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/cytoscape-dagre/2.5.0/cytoscape-dagre.min.js"></script>
  <style>
    :root {
      color-scheme: light;
    }
    body {
      margin: 0;
      display: flex;
      flex-direction: column;
      height: 100vh;
      font-family: 'Inter', 'Segoe UI', system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
      background: #f1f5f9;
      color: #0f172a;
    }
    #app {
      display: flex;
      flex: 1 1 auto;
      min-height: 0;
    }
    #cy {
      flex: 1 1 auto;
      min-height: 100vh;
      background: #f8fafc;
    }
    #side-panel {
      width: 360px;
      max-width: 40vw;
      padding: 18px 20px;
      border-left: 1px solid #e2e8f0;
      background: #fff;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }
    #side-panel h1 {
      margin: 0 0 4px;
      font-size: 18px;
      font-weight: 600;
    }
    #node-title {
      font-size: 16px;
      font-weight: 600;
    }
    #node-image-container {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(136px, 1fr));
      gap: 12px;
    }
    #node-image-container img {
      width: 100%;
      border-radius: 10px;
      background: #e2e8f0;
      object-fit: contain;
      padding: 6px;
      border: 1px solid #cbd5f5;
    }
    details {
      border: 1px solid #e2e8f0;
      border-radius: 10px;
      padding: 10px 12px;
      background: #f8fafc;
    }
    details > summary {
      cursor: pointer;
      font-weight: 600;
      outline: none;
    }
    pre {
      background: transparent;
      border-radius: 6px;
      padding: 0;
      margin: 8px 0 0;
      white-space: pre-wrap;
      word-break: break-word;
      font-size: 12px;
      line-height: 1.45;
    }
    .meta-empty {
      color: #64748b;
      font-style: italic;
      margin: 0;
    }
  </style>
</head>
<body>
  <div id="app">
    <div id="cy"></div>
    <aside id="side-panel">
      <header>
        <h1>__TITLE__</h1>
      </header>
      <div id="node-title">Select a node</div>
      <div id="node-image-container"><p class="meta-empty">No images</p></div>
      <details open>
        <summary>Value</summary>
        <pre id="node-value"></pre>
      </details>
      <details open>
        <summary>Metadata</summary>
        <div id="metadata-content" class="metadata-container"><p class="meta-empty">No metadata</p></div>
      </details>
      <details>
        <summary>Raw Node JSON</summary>
        <pre id="raw-json"></pre>
      </details>
    </aside>
  </div>
  <script>
    const snapshotData = JSON.parse('__SNAPSHOT__');
    const nodes = Array.isArray(snapshotData.nodes) ? snapshotData.nodes : [];
    const edges = Array.isArray(snapshotData.edges) ? snapshotData.edges : [];

    const enhancedNodes = enhanceNodes(nodes);

    const cy = cytoscape({
      container: document.getElementById('cy'),
      elements: enhancedNodes.concat(edges),
      style: [
        { selector: 'node', style: {
            'background-color': '#dbeafe',
            'border-color': '#60a5fa',
            'border-width': 2,
            'shape': 'roundrectangle',
            'label': 'data(label)',
            'text-wrap': 'wrap',
            'text-max-width': 260,
            'color': '#0f172a',
            'font-size': 12,
            'font-weight': 500,
            'padding': '8px',
            'width': 'data(width)',
            'height': 'data(height)'
        } },
        { selector: 'node[image]', style: {
            'background-image': 'data(image)',
            'background-fit': 'cover',
            'background-clip': 'padding-box',
            'background-opacity': 1,
            'border-color': '#f97316'
        } },
        { selector: 'node[is_last]', style: { 'border-color': '#16a34a', 'border-width': 3 } },
        { selector: 'node[is_success]', style: { 'background-color': '#bbf7d0' } },
        { selector: 'node[is_error]', style: { 'background-color': '#fee2e2', 'border-color': '#ef4444' } },
        { selector: 'node:selected', style: { 'border-color': '#1d4ed8', 'border-width': 4 } },
        { selector: 'edge', style: {
            'width': 2,
            'line-color': '#94a3b8',
            'target-arrow-color': '#94a3b8',
            'target-arrow-shape': 'vee',
            'curve-style': 'bezier'
        } }
      ],
      layout: {
        name: 'dagre',
        rankDir: 'TB',
        nodeSep: 80,
        rankSep: 120,
        padding: 60,
        fit: true,
        animate: false
      }
    });

    cy.ready(() => {
      cy.nodes().forEach((node) => {
        const data = node.data();
        if (typeof data.width === 'number') {
          node.style('width', data.width);
        }
        if (typeof data.height === 'number') {
          node.style('height', data.height);
        }
      });
    });

    const titleEl = document.getElementById('node-title');
    const valueEl = document.getElementById('node-value');
    const metadataEl = document.getElementById('metadata-content');
    const rawEl = document.getElementById('raw-json');
    const imageEl = document.getElementById('node-image-container');

    function enhanceNodes(nodes) {
      const scale = window.devicePixelRatio && window.devicePixelRatio > 1 ? 1.2 : 1.0;
      const maxDim = 360;
      const minWidth = 140;
      const minHeight = 80;

      return (nodes || []).map((node) => {
        if (!node || typeof node !== 'object') {
          return node;
        }
        const cloned = JSON.parse(JSON.stringify(node));
        const data = cloned.data || {};
        cloned.data = data;

        if (typeof data.image_width === 'number' && typeof data.image_height === 'number') {
          let width = data.image_width * scale;
          let height = data.image_height * scale;
          const longest = Math.max(width, height);
          if (longest > maxDim) {
            const ratio = maxDim / longest;
            width *= ratio;
            height *= ratio;
          }
          data.width = Math.max(minWidth, Math.round(width));
          data.height = Math.max(minHeight, Math.round(height));
        } else {
          data.width = minWidth;
          data.height = minHeight;
        }

        return cloned;
      });
    }

    function escapeHtml(value) {
      return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    function formatJson(value) {
      try {
        return JSON.stringify(value, null, 2);
      } catch (err) {
        return String(value ?? '');
      }
    }

    function renderMetadata(meta) {
      if (!meta || typeof meta !== 'object' || Array.isArray(meta)) {
        return `<pre>${escapeHtml(formatJson(meta))}</pre>`;
      }
      const entries = Object.entries(meta);
      if (!entries.length) {
        return '<p class="meta-empty">No metadata</p>';
      }
      return entries.map(([key, value]) => `
        <details open>
          <summary>${escapeHtml(key)}</summary>
          <pre>${escapeHtml(formatJson(value))}</pre>
        </details>
      `).join('');
    }

    function renderImages(data) {
      const fragments = [];
      const seen = new Set();

      if (data.value_image) {
        seen.add(data.value_image);
        fragments.push(`<img src="${data.value_image}" alt="Node value" loading="lazy" />`);
      } else if (data.image) {
        seen.add(data.image);
        fragments.push(`<img src="${data.image}" alt="Node preview" loading="lazy" />`);
      }

      if (Array.isArray(data.meta_images)) {
        data.meta_images.forEach((src, index) => {
          if (!src || seen.has(src)) {
            return;
          }
          seen.add(src);
          fragments.push(`<img src="${src}" alt="Metadata image ${index + 1}" loading="lazy" />`);
        });
      }

      return fragments.join('') || '<p class="meta-empty">No images</p>';
    }

    function renderNode(node) {
      if (!node || !node.data) {
        titleEl.textContent = 'Select a node';
        valueEl.textContent = '';
        metadataEl.innerHTML = '<p class="meta-empty">No metadata</p>';
        imageEl.innerHTML = '<p class="meta-empty">No images</p>';
        rawEl.textContent = '';
        return;
      }

      const data = node.data();
      titleEl.textContent = data.label || data.id || 'Node';
      valueEl.textContent = data.value_repr || '';
      metadataEl.innerHTML = renderMetadata(data.meta || {});
      imageEl.innerHTML = renderImages(data);
      rawEl.textContent = formatJson(data);
    }

    cy.on('tap', 'node', (event) => {
      cy.nodes().unselect();
      const node = event.target;
      node.select();
      renderNode(node);
    });

    const defaultNodeId = snapshotData.final_node_id
      || (enhancedNodes.length ? enhancedNodes[enhancedNodes.length - 1].data?.id : null)
      || (cy.nodes().length ? cy.nodes()[0].id() : null);

    if (defaultNodeId) {
      const node = cy.getElementById(defaultNodeId);
      if (node && node.length) {
        node.select();
        renderNode(node);
        cy.center(node);
      } else if (cy.nodes().length) {
        const first = cy.nodes()[0];
        first.select();
        renderNode(first);
      } else {
        renderNode(null);
      }
    } else {
      renderNode(null);
    }
  </script>
</body>
</html>"#;

    let mut html = template.replace("__TITLE__", &title_html);
    html = html.replace("__SNAPSHOT__", &snapshot_json_safe);

    Ok(html)
}

#[pymodule]
fn _webui_snapshot(_py: Python<'_>, module: &PyModule) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(build_snapshot, module)?)?;
    module.add_function(wrap_pyfunction!(build_snapshot_html_v2, module)?)?;
    Ok(())
}

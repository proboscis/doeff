//! Python API for doeff-indexer using PyO3
//!
//! This module provides Python bindings for the indexer, allowing Python code
//! to query for symbols (functions and variables) with specific tags/markers.

use pyo3::prelude::*;
use std::path::PathBuf;

use crate::indexer::{build_index, Index, IndexEntry, ItemKind};

/// Information about a discovered symbol (function or variable)
#[pyclass]
#[derive(Clone)]
pub struct SymbolInfo {
    /// Symbol name (e.g., "my_interpreter")
    #[pyo3(get)]
    pub name: String,

    /// Module path (e.g., "some.module.a.b")
    #[pyo3(get)]
    pub module_path: String,

    /// Full qualified path (module_path.name)
    #[pyo3(get)]
    pub full_path: String,

    /// Symbol type: "function", "async_function", or "variable"
    #[pyo3(get)]
    pub symbol_type: String,

    /// Tags/markers from comments or docstrings (e.g., ["doeff", "interpreter", "default"])
    #[pyo3(get)]
    pub tags: Vec<String>,

    /// Line number in source file
    #[pyo3(get)]
    pub line_number: usize,

    /// File path
    #[pyo3(get)]
    pub file_path: String,
}

impl SymbolInfo {
    fn from_index_entry(entry: &IndexEntry) -> Self {
        let symbol_type = match entry.item_kind {
            ItemKind::Function => "function",
            ItemKind::AsyncFunction => "async_function",
            ItemKind::Assignment => "variable",
        };

        SymbolInfo {
            name: entry.name.clone(),
            module_path: module_path_from_qualified(&entry.qualified_name),
            full_path: entry.qualified_name.clone(),
            symbol_type: symbol_type.to_string(),
            tags: entry.markers.clone(),
            line_number: entry.line,
            file_path: entry.file_path.clone(),
        }
    }
}

#[pymethods]
impl SymbolInfo {
    fn __repr__(&self) -> String {
        format!(
            "SymbolInfo(name='{}', module_path='{}', symbol_type='{}', tags={:?})",
            self.name, self.module_path, self.symbol_type, self.tags
        )
    }
}

/// Indexer for discovering symbols in a Python project
#[pyclass]
pub struct Indexer {
    index: Index,
}

#[pymethods]
impl Indexer {
    /// Create an indexer for a specific module path
    ///
    /// Args:
    ///     module_path: Python module path (e.g., "some.module.a.b.c")
    ///
    /// Returns:
    ///     Indexer instance
    ///
    /// Example:
    ///     >>> indexer = Indexer.for_module("myproject.core")
    #[staticmethod]
    fn for_module(module_path: &str) -> PyResult<Self> {
        // Resolve module path to file system path
        let root_path = resolve_module_to_path(module_path)?;

        // Build index from root path
        let index = build_index(&root_path)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;

        Ok(Indexer { index })
    }

    /// Find symbols matching the given tags/markers
    ///
    /// Args:
    ///     tags: List of tags to match (e.g., ["doeff", "interpreter"])
    ///     symbol_type: Optional filter by symbol type ("function", "async_function", "variable")
    ///
    /// Returns:
    ///     List of SymbolInfo matching the criteria
    ///
    /// Example:
    ///     >>> symbols = indexer.find_symbols(tags=["doeff", "interpreter", "default"])
    ///     >>> for sym in symbols:
    ///     ...     print(f"{sym.full_path} at line {sym.line_number}")
    fn find_symbols(
        &self,
        tags: Vec<String>,
        symbol_type: Option<String>,
    ) -> PyResult<Vec<SymbolInfo>> {
        let results: Vec<SymbolInfo> = self
            .index
            .entries
            .iter()
            .filter(|entry| {
                // Filter by tags
                if !matches_all_tags(entry, &tags) {
                    return false;
                }

                // Filter by symbol type if specified
                if let Some(ref st) = symbol_type {
                    let entry_type = match entry.item_kind {
                        ItemKind::Function => "function",
                        ItemKind::AsyncFunction => "async_function",
                        ItemKind::Assignment => "variable",
                    };
                    if entry_type != st {
                        return false;
                    }
                }

                true
            })
            .map(SymbolInfo::from_index_entry)
            .collect();

        Ok(results)
    }

    /// Get the module hierarchy for the indexed module
    ///
    /// Returns a list of module paths from root to the indexed module.
    ///
    /// Returns:
    ///     List of module paths in hierarchy order
    ///
    /// Example:
    ///     >>> indexer = Indexer.for_module("some.module.a.b.c")
    ///     >>> indexer.get_module_hierarchy()
    ///     ['some', 'some.module', 'some.module.a', 'some.module.a.b', 'some.module.a.b.c']
    fn get_module_hierarchy(&self) -> PyResult<Vec<String>> {
        // Extract module path from first entry (all entries share same root)
        if let Some(first_entry) = self.index.entries.first() {
            let module_path = module_path_from_qualified(&first_entry.qualified_name);
            Ok(build_module_hierarchy(&module_path))
        } else {
            Ok(vec![])
        }
    }

    /// Find symbols within a specific module matching the given tags
    ///
    /// Args:
    ///     module: Module path to search within (e.g., "some.module.a")
    ///     tags: List of tags to match
    ///
    /// Returns:
    ///     List of SymbolInfo matching the criteria in the specified module
    ///
    /// Example:
    ///     >>> symbols = indexer.find_in_module("myproject.core", tags=["doeff", "default"])
    fn find_in_module(&self, module: &str, tags: Vec<String>) -> PyResult<Vec<SymbolInfo>> {
        let results: Vec<SymbolInfo> = self
            .index
            .entries
            .iter()
            .filter(|entry| {
                // Filter by module path
                let entry_module = module_path_from_qualified(&entry.qualified_name);
                if entry_module != module {
                    return false;
                }

                // Filter by tags
                matches_all_tags(entry, &tags)
            })
            .map(SymbolInfo::from_index_entry)
            .collect();

        Ok(results)
    }

    fn __repr__(&self) -> String {
        format!(
            "Indexer(root='{}', entries={})",
            self.index.root,
            self.index.entries.len()
        )
    }
}

// Helper functions

fn matches_all_tags(entry: &IndexEntry, tags: &[String]) -> bool {
    // Check if entry contains all the specified tags (case-insensitive)
    tags.iter()
        .all(|tag| entry.markers.iter().any(|m| m.eq_ignore_ascii_case(tag)))
}

fn module_path_from_qualified(qualified_name: &str) -> String {
    // Extract module path from qualified name (e.g., "some.module.a.func" -> "some.module.a")
    qualified_name
        .rsplitn(2, '.')
        .nth(1)
        .unwrap_or("")
        .to_string()
}

fn build_module_hierarchy(module_path: &str) -> Vec<String> {
    if module_path.is_empty() {
        return vec![];
    }

    let parts: Vec<&str> = module_path.split('.').collect();
    let mut hierarchy = Vec::new();

    for i in 1..=parts.len() {
        hierarchy.push(parts[..i].join("."));
    }

    hierarchy
}

fn resolve_module_to_path(module_path: &str) -> PyResult<PathBuf> {
    // For now, assume current working directory
    // TODO: Implement proper module resolution (sys.path, UV project detection, etc.)
    let path = PathBuf::from(".");

    if !path.exists() {
        return Err(PyErr::new::<pyo3::exceptions::PyFileNotFoundError, _>(
            format!("Module path not found: {}", module_path),
        ));
    }

    Ok(path)
}

/// Python module definition
#[pymodule]
fn doeff_indexer(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Indexer>()?;
    m.add_class::<SymbolInfo>()?;
    Ok(())
}

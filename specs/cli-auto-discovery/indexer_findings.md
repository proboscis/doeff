# Doeff-Indexer Investigation Findings

> **Status**: ✅ IMPLEMENTED - This was the Phase 0 investigation document.
> All identified gaps have been addressed. See [IMPLEMENTATION_STATUS.md](./IMPLEMENTATION_STATUS.md).

## Current State (packages/doeff-indexer/) - As Investigated

### ✅ What Exists

1. **Mature Rust Implementation**
   - Used by IDE plugins (PyCharm)
   - AST parsing with rustpython-parser
   - Function categorization: Interpreter, Transform, KleisliProgram, Interceptor
   - Marker extraction from comments: `# doeff: interpreter`
   - CLI commands: `find-interpreters`, `find-transforms`, `find-kleisli`, `find-interceptors`

2. **Core Capabilities**
   - Parse Python source files
   - Extract function signatures
   - Detect `@do` decorator
   - Extract markers from same-line comments
   - Categorize by signature patterns
   - Module path resolution (UV projects, regular packages)

3. **Data Structures**
   ```rust
   struct IndexEntry {
       name: String,
       module_path: String,
       file_path: String,
       line_number: u32,
       decorators: Vec<String>,
       return_annotation: Option<String>,
       all_parameters: Vec<Parameter>,
       markers: Vec<String>,  // From comments
       categories: Vec<EntryCategory>,
   }
   ```

### ❌ What's Missing (Needed for Features)

1. **PyO3 Python API**
   - No Python bindings currently
   - CLI-only interface
   - Cannot be imported from Python
   - Need `from doeff_indexer import Indexer`

2. **Docstring Parsing**
   - Currently only parses same-line comments
   - `def foo(): # doeff: interpreter` ✅
   - `def foo():\n    '''# doeff: interpreter'''` ❌
   - Need to support markers in docstrings

3. **Variable/Symbol Indexing**
   - Currently only indexes functions
   - Need to index module-level variables
   - Need to support: `default_env: Program[dict]  # doeff: default`

4. **New Marker Support**
   - Need `# doeff: default` for envs
   - Need `# doeff: interpreter, default` for default interpreters
   - Currently has: `interpreter`, `transform`, `kleisli`, `interceptor`

5. **Module Hierarchy API**
   - No API to get module hierarchy
   - Need: `get_module_hierarchy("some.module.a.b.c")` → `["some", "some.module", ...]`

6. **Module-Filtered Queries**
   - Can filter by marker/type
   - Need: find symbols in specific module only
   - Need: `find_in_module("some.module.a", tags=["doeff", "default"])`

## Required Enhancements

### 1. PyO3 Bindings (High Priority)

**Add to Cargo.toml**:
```toml
[dependencies]
pyo3 = { version = "0.20", features = ["extension-module"] }

[lib]
crate-type = ["cdylib"]  # For Python extension
```

**Implement**:
```rust
#[pyclass]
#[derive(Clone)]
pub struct SymbolInfo {
    #[pyo3(get)]
    pub name: String,
    #[pyo3(get)]
    pub module_path: String,
    #[pyo3(get)]
    pub full_path: String,
    #[pyo3(get)]
    pub symbol_type: String,  // "function" | "variable"
    #[pyo3(get)]
    pub tags: Vec<String>,
    #[pyo3(get)]
    pub line_number: usize,
}

#[pyclass]
pub struct Indexer {
    root_path: String,
}

#[pymethods]
impl Indexer {
    #[staticmethod]
    fn for_module(module_path: &str) -> PyResult<Self> { ... }

    fn find_symbols(
        &self,
        tags: Vec<String>,
        symbol_type: Option<String>
    ) -> PyResult<Vec<SymbolInfo>> { ... }

    fn get_module_hierarchy(&self) -> PyResult<Vec<String>> { ... }

    fn find_in_module(
        &self,
        module: &str,
        tags: Vec<String>
    ) -> PyResult<Vec<SymbolInfo>> { ... }
}

#[pymodule]
fn doeff_indexer(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_class::<Indexer>()?;
    m.add_class::<SymbolInfo>()?;
    Ok(())
}
```

**Build with maturin**:
```bash
pip install maturin
cd packages/doeff-indexer
maturin develop  # Development build
maturin build --release  # Production build
```

### 2. Docstring Parsing (Medium Priority)

**Current**: Only extracts markers from same-line comments
```python
def foo(prog: Program[int]) -> int:  # doeff: interpreter ✅
```

**Needed**: Extract from docstrings
```python
def foo(prog: Program[int]) -> int:
    """
    My function description.
    # doeff: interpreter, default
    """  # ✅ Need to support this
```

**Implementation**:
```rust
fn extract_markers_from_docstring(function_def: &FunctionDef) -> Vec<String> {
    if let Some(docstring) = extract_docstring(function_def) {
        parse_markers_from_string(&docstring)
    } else {
        vec![]
    }
}

fn extract_docstring(function_def: &FunctionDef) -> Option<String> {
    // Check first statement for string constant
    if let Some(first_stmt) = function_def.body.first() {
        if let Stmt::Expr { value, .. } = first_stmt {
            if let Expr::Constant { value: Constant::Str(s), .. } = value {
                return Some(s.clone());
            }
        }
    }
    None
}
```

### 3. Variable Indexing (High Priority)

**Current**: Only indexes functions

**Needed**: Index module-level variables
```python
# some/module/__init__.py
default_env: Program[dict] = Program.pure({"key": "value"})  # doeff: default
```

**Implementation**:
```rust
fn index_module(source: &str) -> Vec<IndexEntry> {
    let ast = parse(source)?;
    let mut entries = vec![];

    for stmt in ast.body {
        match stmt {
            Stmt::FunctionDef(func) => {
                entries.push(index_function(&func));
            }
            Stmt::AnnAssign { target, annotation, value, .. } => {
                // NEW: Index annotated assignments
                if let Some(entry) = index_variable(target, annotation, value, source) {
                    entries.push(entry);
                }
            }
            Stmt::Assign { targets, value, .. } => {
                // NEW: Index regular assignments
                for target in targets {
                    if let Some(entry) = index_assignment(target, value, source) {
                        entries.push(entry);
                    }
                }
            }
            _ => {}
        }
    }

    entries
}

fn index_variable(
    target: &Expr,
    annotation: &Option<Box<Expr>>,
    value: &Option<Box<Expr>>,
    source: &str
) -> Option<IndexEntry> {
    // Extract variable name
    let name = match target {
        Expr::Name { id, .. } => id,
        _ => return None,
    };

    // Extract markers from same-line comment
    let markers = extract_markers_from_source(source, line_number, name);

    Some(IndexEntry {
        name: name.clone(),
        symbol_type: "variable",
        markers,
        // ... other fields
    })
}
```

### 4. New Marker Support (Medium Priority)

**Add to marker parsing**:
- `default` - for default envs
- Support comma-separated: `# doeff: interpreter, default`

**Already supported format**:
```python
def foo():  # doeff: transform, kleisli
    # Multiple markers work
```

**Just need to add**:
- Recognition of `default` marker
- Documentation update

### 5. Module Hierarchy Utility (Medium Priority)

**Implementation**:
```rust
impl Indexer {
    pub fn get_module_hierarchy(&self, target_module: &str) -> Vec<String> {
        let parts: Vec<&str> = target_module.split('.').collect();
        let mut hierarchy = vec![];

        for i in 1..=parts.len() {
            hierarchy.push(parts[..i].join("."));
        }

        hierarchy
    }
}

// Example:
// get_module_hierarchy("some.module.a.b.c")
// → ["some", "some.module", "some.module.a", "some.module.a.b", "some.module.a.b.c"]
```

### 6. Module-Filtered Queries (Low Priority - can filter in Python)

**Option 1: Rust Implementation**
```rust
impl Indexer {
    pub fn find_in_module(
        &self,
        module: &str,
        tags: Vec<String>
    ) -> Vec<SymbolInfo> {
        self.find_symbols(tags, None)
            .into_iter()
            .filter(|s| s.module_path == module)
            .collect()
    }
}
```

**Option 2: Python Implementation** (simpler)
```python
# In Python
all_symbols = indexer.find_symbols(tags=["doeff", "default"])
module_symbols = [s for s in all_symbols if s.module_path == "some.module"]
```

## Implementation Priority

### Phase 1 (Blocking): PyO3 Bindings
- Cannot proceed without Python API
- Estimated: 1-2 days
- Enables all downstream work

### Phase 2 (High): Variable Indexing
- Critical for env discovery
- Estimated: 1 day
- Depends on: Phase 1

### Phase 3 (High): Docstring Parsing
- Preferred way to mark functions
- Estimated: 1 day
- Can work in parallel with Phase 2

### Phase 4 (Medium): Module Hierarchy
- Needed for discovery logic
- Estimated: 0.5 days
- Can implement in Python if needed

### Phase 5 (Low): Module Filtering
- Nice to have, can filter in Python
- Estimated: 0.5 days
- Optional optimization

## Testing Requirements

### Rust Tests
```rust
#[test]
fn test_docstring_parsing() {
    let source = r#"
def foo(prog: Program[int]) -> int:
    '''
    # doeff: interpreter, default
    '''
    pass
"#;
    let entries = index_source(source);
    assert_eq!(entries[0].markers, vec!["interpreter", "default"]);
}

#[test]
fn test_variable_indexing() {
    let source = r#"
default_env: Program[dict] = Program.pure({})  # doeff: default
"#;
    let entries = index_source(source);
    assert_eq!(entries[0].symbol_type, "variable");
    assert_eq!(entries[0].markers, vec!["default"]);
}
```

### Python Integration Tests
```python
def test_indexer_python_api():
    from doeff_indexer import Indexer, SymbolInfo

    indexer = Indexer.for_module("test_module")
    symbols = indexer.find_symbols(tags=["doeff", "interpreter", "default"])

    assert len(symbols) > 0
    assert all(isinstance(s, SymbolInfo) for s in symbols)

def test_module_hierarchy():
    from doeff_indexer import Indexer

    indexer = Indexer.for_module("some.module.a.b.c")
    hierarchy = indexer.get_module_hierarchy()

    assert hierarchy == [
        "some",
        "some.module",
        "some.module.a",
        "some.module.a.b",
        "some.module.a.b.c"
    ]
```

## Backward Compatibility

### CLI Commands (Unchanged)
- `find-interpreters` - still works
- `find-transforms` - still works
- `find-kleisli` - still works
- `find-interceptors` - still works

### IDE Plugins (Unaffected)
- PyCharm plugin continues to work
- Uses existing CLI interface
- Can optionally migrate to Python API later

### New Features (Opt-in)
- PyO3 API is addition, not modification
- Variable indexing adds new entries, doesn't change function entries
- Docstring parsing supplements comment parsing
- Fully backward compatible

## Build Configuration

**Update Cargo.toml**:
```toml
[package]
name = "doeff-indexer"
version = "0.2.0"  # Bump for new features
edition = "2021"

[lib]
name = "doeff_indexer"
crate-type = ["cdylib", "rlib"]  # cdylib for Python, rlib for Rust

[dependencies]
pyo3 = { version = "0.20", features = ["extension-module"] }
# ... existing dependencies

[package.metadata.maturin]
name = "doeff-indexer"
python-source = "python"  # Optional Python wrapper code
```

**Build Commands**:
```bash
# Development
cd packages/doeff-indexer
maturin develop

# Production
maturin build --release --out dist/

# Install
pip install dist/doeff_indexer-*.whl
```

## Summary

**Existing Strengths**:
- Mature, battle-tested Rust implementation
- Used by IDE plugins in production
- Comprehensive function categorization
- Fast and reliable

**Required Additions** (As Identified):
1. ✅ **PyO3 bindings** - enables Python CLI to use indexer
2. ✅ **Docstring parsing** - preferred marker location
3. ✅ **Variable indexing** - discover env declarations
4. ✅ **Module hierarchy API** - for discovery traversal
5. ⚠️ **Module filtering** - nice to have, can do in Python

**Original Estimate**: 3-4 days of Rust development
**Actual Result**: Completed in Phase 2 (Commit `5d09215`)
**Impact**: ✅ Unblocked all CLI features, maintained backward compatibility

---

## Implementation Results (Added October 2025)

### What Was Built

1. **PyO3 Bindings** ✅
   - Created `python_api.rs` (269 lines)
   - `Indexer` class with `for_module()` and `find_symbols()`
   - `SymbolInfo` class with all metadata
   - Installed via `uvx maturin develop --release`

2. **Docstring Parsing** ✅
   - Already existed! `extract_markers_from_docstring()` was present
   - Enhanced to work with both functions and variables
   - No changes needed

3. **Variable Indexing** ✅
   - Added `analyze_assignment()` function
   - Indexes annotated assignments with `Program` types
   - Indexes variables with `# doeff:` markers
   - Conservative approach (only typed or marked variables)

4. **Module Hierarchy** ✅
   - Implemented via Python-side `_get_module_hierarchy()`
   - Returns list from root to module
   - Used for closest interpreter selection

5. **Module Filtering** ✅
   - Implemented in Python discovery layer
   - No changes to indexer needed

### Files Created/Modified

**Created**:
- `packages/doeff-indexer/src/python_api.rs` (269 lines)

**Modified**:
- `packages/doeff-indexer/Cargo.toml` (added PyO3 features)
- `packages/doeff-indexer/src/lib.rs` (exposed python_api module)
- `packages/doeff-indexer/src/indexer.rs` (variable indexing)

### Performance

- Discovery overhead: < 100ms (excellent)
- No caching needed for v1
- Rust indexer remains very fast

### Backward Compatibility

✅ **Fully maintained**:
- CLI commands unchanged (IDE plugins work)
- Existing function indexing unchanged
- PyO3 API is pure addition
- No breaking changes to Rust API

# doeff-indexer Specification

## Table of Contents

1. [Purpose and Overview](#purpose-and-overview)
2. [Type Definitions](#type-definitions)
3. [Detection Logic](#detection-logic)
4. [CLI Commands](#cli-commands)
5. [Type Filtering Rules](#type-filtering-rules)
6. [@do Decorator Handling](#do-decorator-handling)
7. [Integration with IDE Plugins](#integration-with-ide-plugins)
8. [Examples and Edge Cases](#examples-and-edge-cases)

## Purpose and Overview

`doeff-indexer` is a static analysis tool that indexes Python source code to identify and categorize functions related to the `doeff` effects system. It provides language server capabilities for IDE integration and command-line tools for developers to discover doeff-related functions in their codebase.

### Core Responsibilities

1. **Static Analysis**: Parse Python source files to extract function definitions, decorators, type annotations, and comments
2. **Pattern Recognition**: Identify doeff-specific patterns like `@do` decorators and marker comments
3. **Type Classification**: Categorize functions into four main types: Interpreters, Transforms, KleisliProgram, and Interceptors
4. **CLI Interface**: Provide command-line tools for querying the indexed functions
5. **IDE Integration**: Support language server protocol for real-time IDE assistance

### Key Features

- **Signature-based categorization**: Analyzes function signatures to determine categories
- **Marker-based filtering**: Uses `# doeff: <type>` comments for explicit marking
- **@do decorator handling**: Special logic for functions decorated with `@do`
- **Type filtering**: Support for filtering functions by parameter types
- **Module path resolution**: Handles various Python project structures (UV projects, regular packages)

## Type Definitions

### Core Function Categories

The indexer categorizes functions into four primary types based on their signatures and purpose:

#### 1. Interpreter
```python
def interpreter_function(program: Program[T]) -> T:
    """
    Interprets a Program[T] and returns the unwrapped value T.
    Does NOT return Program type.
    """
```

**Characteristics:**
- First parameter: `Program[T]` (any generic Program type)
- Return type: Any type except `Program` 
- Purpose: Execute/interpret programs to produce concrete values

#### 2. Transform
```python
def transform_function(program: Program[T]) -> Program[U]:
    """
    Transforms one Program into another Program.
    Input and output are both Program types.
    """
```

**Characteristics:**
- First parameter: `Program[T]` (any generic Program type)
- Return type: `Program[U]` (any generic Program type)
- Purpose: Transform programs while maintaining the Program wrapper

#### 3. KleisliProgram
```python
@do
def kleisli_function(value: T) -> U:
    """
    Creates a Program[U] from a value T using @do notation.
    The @do decorator wraps the return value in Program.
    """
```

**Characteristics:**
- First parameter: Any type except `Program` or `Effect`
- Decorator: `@do` (automatic categorization)
- Alternative: Manual marking with `# doeff: kleisli`
- Purpose: Lift regular values into the Program monad

#### 4. Interceptor
```python
def interceptor_function(effect: Effect) -> Effect | Program:
    """
    Intercepts and potentially modifies effects during program execution.
    First parameter is always an Effect type.
    """
```

**Characteristics:**
- First parameter: `Effect` (any Effect subtype)
- Return type: `Effect` or `Program` (flexible)
- Purpose: Intercept and modify effects during execution

### EntryCategory Enumeration

```rust
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum EntryCategory {
    // Primary categories (mutually exclusive for core types)
    ProgramInterpreter,    // Executes Program[T] -> T
    ProgramTransformer,    // Transforms Program[T] -> Program[U]  
    KleisliProgram,        // @do functions or T -> Program[U]
    Interceptor,           // Effect -> Effect | Program
    
    // Secondary categories (can be combined)
    DoFunction,            // Has @do decorator
    AcceptsProgramParam,   // First param is Program[T]
    ReturnsProgram,        // Return type is Program[T]
    AcceptsEffectParam,    // First param is Effect
    
    // Marker categories
    HasMarker,             // Has any doeff: marker comment
}
```

### Index Entry Structure

```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IndexEntry {
    pub name: String,                    // Function name
    pub module_path: String,             // Fully qualified module path
    pub file_path: String,               // Absolute file path
    pub line_number: u32,                // Line where function is defined
    pub decorators: Vec<String>,         // List of decorator names
    pub return_annotation: Option<String>, // Return type annotation
    pub all_parameters: Vec<Parameter>,  // All function parameters
    pub markers: Vec<String>,            // Extracted doeff markers
    pub categories: Vec<EntryCategory>,  // Assigned categories
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Parameter {
    pub name: String,                    // Parameter name
    pub annotation: Option<String>,      // Type annotation
    pub default_value: Option<String>,   // Default value if any
    pub is_vararg: bool,                 // *args parameter
    pub is_kwarg: bool,                  // **kwargs parameter
}
```

## Detection Logic

The indexer employs two complementary detection strategies:

### 1. Signature-based Categorization

This is the primary method for categorizing functions based on their type signatures:

```rust
fn categorize_by_signature(entry: &mut IndexEntry) {
    let first_param = entry.all_parameters.first();
    let return_type = &entry.return_annotation;
    let has_do = entry.decorators.contains(&"do".to_string());
    
    if let Some(param) = first_param {
        if let Some(annotation) = &param.annotation {
            if annotation.contains("Program") {
                // Program as first parameter
                entry.categories.push(EntryCategory::AcceptsProgramParam);
                
                if has_do {
                    // @do with Program -> Transform
                    entry.categories.push(EntryCategory::ProgramTransformer);
                } else if let Some(ret) = return_type {
                    if ret.contains("Program") {
                        entry.categories.push(EntryCategory::ProgramTransformer);
                    } else {
                        entry.categories.push(EntryCategory::ProgramInterpreter);
                    }
                }
            } else if annotation.contains("Effect") {
                // Effect as first parameter
                entry.categories.push(EntryCategory::AcceptsEffectParam);
                entry.categories.push(EntryCategory::Interceptor);
            } else if has_do {
                // @do with non-Program/Effect -> Kleisli
                entry.categories.push(EntryCategory::KleisliProgram);
            }
        }
    }
    
    // Check return type
    if let Some(ret) = return_type {
        if ret.contains("Program") {
            entry.categories.push(EntryCategory::ReturnsProgram);
        }
    }
    
    // Mark @do functions
    if has_do {
        entry.categories.push(EntryCategory::DoFunction);
    }
}
```

#### Detection Rules

1. **Interpreter Detection**:
   - First parameter: `Program[T]` 
   - Return type: NOT `Program`
   - Category: `ProgramInterpreter`

2. **Transform Detection**:
   - First parameter: `Program[T]`
   - Return type: `Program[U]` OR `@do` decorator
   - Category: `ProgramTransformer`

3. **Kleisli Detection**:
   - `@do` decorator with non-Program/Effect first parameter
   - OR manual `# doeff: kleisli` marker
   - Category: `KleisliProgram`

4. **Interceptor Detection**:
   - First parameter: `Effect` (any subtype)
   - Category: `Interceptor`

### 2. Marker-based Filtering

The indexer extracts explicit markers from comments to override or supplement signature-based categorization:

#### Marker Format

```python
def my_function(param: Type) -> ReturnType:  # doeff: interpreter
def another_function(x: int) -> str:  # doeff: kleisli, transform
```

#### Supported Markers

- `interpreter`: Mark function as interpreter
- `transform`: Mark function as transform  
- `kleisli`: Mark function as Kleisli program
- `interceptor`: Mark function as interceptor

#### Marker Extraction Algorithm

```rust
fn extract_markers_from_source(
    source: &str, 
    line_number: u32, 
    function_name: &str,
    args: &Arguments
) -> Vec<String> {
    let lines: Vec<&str> = source.lines().collect();
    let mut markers = Vec::new();
    
    // Search for markers in function signature lines
    let start_line = line_number as usize - 1;
    let end_line = find_function_end(source, start_line, args);
    
    for i in start_line..=end_line {
        if let Some(line) = lines.get(i) {
            if let Some(comment_start) = line.find('#') {
                let comment = &line[comment_start..];
                if let Some(doeff_start) = comment.to_lowercase().find("doeff:") {
                    let marker_text = &comment[doeff_start + 6..];
                    for marker in marker_text.split(',') {
                        let cleaned = marker.trim();
                        if !cleaned.is_empty() {
                            markers.push(cleaned.to_string());
                        }
                    }
                }
            }
        }
    }
    
    markers
}
```

#### Marker Precedence

- Markers take precedence over signature analysis for `find-*` commands
- Signature analysis is used for categorization regardless of markers
- Functions without markers are categorized but not returned by `find-*` commands

## CLI Commands

### Core Commands

#### `doeff-indexer --root <path>`

Builds a complete index and outputs JSON containing all discovered functions.

```bash
doeff-indexer --root /project/path
```

**Output Format:**
```json
{
  "entries": [
    {
      "name": "exec_program",
      "module_path": "myproject.interpreter", 
      "file_path": "/project/myproject/interpreter.py",
      "line_number": 15,
      "decorators": [],
      "return_annotation": "int",
      "all_parameters": [
        {
          "name": "program",
          "annotation": "Program[int]",
          "default_value": null,
          "is_vararg": false,
          "is_kwarg": false
        }
      ],
      "markers": ["interpreter"],
      "categories": ["ProgramInterpreter", "AcceptsProgramParam", "HasMarker"]
    }
  ]
}
```

#### `find-interpreters <root_path>`

Returns only functions marked with `# doeff: interpreter`.

```bash
find-interpreters /project/path
```

**Filtering Logic:**
- Must have `interpreter` in markers array
- Signature-based categorization is ignored
- Returns subset of index entries

#### `find-transforms <root_path>`  

Returns only functions marked with `# doeff: transform`.

```bash
find-transforms /project/path
```

#### `find-kleisli <root_path>`

Returns functions marked with `# doeff: kleisli` OR having `@do` decorator.

```bash
find-kleisli /project/path
```

**Filtering Logic:**
- Has `kleisli` in markers array, OR
- Has `DoFunction` in categories (any `@do` function)

#### `find-kleisli --type-arg <type> <root_path>`

Returns Kleisli functions whose first parameter matches the specified type.

```bash
find-kleisli --type-arg str /project/path
find-kleisli --type-arg User /project/path
```

**Type Matching Rules:**
1. Exact match: `str` matches `str`
2. Generic match: `str` matches `Optional[str]`, `List[str]`  
3. Any match: `Any` type matches all type filters
4. Case-sensitive matching

#### `find-interceptors <root_path>`

Returns only functions marked with `# doeff: interceptor`.

```bash
find-interceptors /project/path
```

### Command Behavior

All `find-*` commands:
- Build the index from scratch each time
- Apply marker-based filtering (except `find-kleisli` which also includes `@do`)
- Return JSON arrays of matching entries
- Exit with status 0 on success, non-zero on error

## Type Filtering Rules

### Type Annotation Matching

The indexer supports sophisticated type matching for parameter filtering:

#### Exact Type Matching
```python
# find-kleisli --type-arg str matches:
@do
def process_string(value: str) -> int:  # ✅ Exact match
    return len(value)
```

#### Generic Type Matching  
```python
# find-kleisli --type-arg str matches:
@do  
def process_optional(value: Optional[str]) -> int:  # ✅ Contains str
    return len(value or "")

@do
def process_list(items: List[str]) -> int:  # ✅ Contains str  
    return len(items)
```

#### Any Type Special Handling
```python
@do
def process_any(value: Any) -> str:  # ✅ Matches ALL type filters
    return str(value)
```

**Any Matching Logic:**
- Functions with `Any` type parameter match all `--type-arg` filters
- This allows generic functions to appear in all type-specific searches

#### Union Type Handling
```python
@do
def process_union(value: Union[str, int]) -> str:  # ✅ Matches --type-arg str
    return str(value)
```

#### Complex Generics
```python  
@do
def process_complex(data: Dict[str, List[User]]) -> Summary:  # ✅ Matches --type-arg User
    return analyze(data)
```

### Type Matching Algorithm

```rust
fn matches_type_filter(annotation: &str, type_filter: &str) -> bool {
    // Special case: Any matches everything
    if annotation.contains("Any") {
        return true;
    }
    
    // Direct match
    if annotation == type_filter {
        return true;
    }
    
    // Generic/container match (List[str], Optional[str], etc.)
    if annotation.contains(type_filter) {
        return true;
    }
    
    false
}
```

## @do Decorator Handling

The `@do` decorator receives special treatment in the indexer as it fundamentally changes function semantics.

### @do Detection

```rust
fn extract_decorators(function_def: &FunctionDef) -> Vec<String> {
    function_def.decorator_list
        .iter()
        .map(|decorator| match decorator {
            Expr::Name { id, .. } => id.to_string(),
            Expr::Attribute { attr, .. } => attr.to_string(),
            _ => "unknown".to_string(),
        })
        .collect()
}
```

### @do Categorization Logic

Functions with `@do` decorator are categorized based on their first parameter:

```python
# Case 1: @do with Program parameter -> Transform
@do
def transform_program(program: Program[int]) -> str:
    """Automatically categorized as ProgramTransformer"""
    result = yield program
    return str(result)

# Case 2: @do with Effect parameter -> Interceptor  
@do
def intercept_effect(effect: LogEffect) -> str:
    """Automatically categorized as Interceptor"""
    yield effect
    return "logged"

# Case 3: @do with other parameter -> KleisliProgram
@do  
def kleisli_function(user_id: str) -> User:
    """Automatically categorized as KleisliProgram"""
    yield Log(f"Fetching {user_id}")
    return User(user_id)
```

### @do Override Rules

1. **Transform Override**: `@do` + `Program` parameter = `ProgramTransformer` (even if return type is not `Program`)
2. **Interceptor Override**: `@do` + `Effect` parameter = `Interceptor`  
3. **Kleisli Default**: `@do` + other parameter = `KleisliProgram`

### @do in find-kleisli

The `find-kleisli` command has special logic to include `@do` functions:

```rust
fn find_kleisli(entries: &[IndexEntry]) -> Vec<IndexEntry> {
    entries.iter()
        .filter(|entry| {
            // Include if marked with kleisli
            entry.markers.iter().any(|m| m.eq_ignore_ascii_case("kleisli")) ||
            // OR if it has @do decorator (regardless of first parameter type)
            entry.categories.contains(&EntryCategory::DoFunction)
        })
        .cloned()
        .collect()
}
```

This means ALL `@do` functions appear in `find-kleisli` results, even if they're categorized as Transforms or Interceptors.

## Integration with IDE Plugins

### Language Server Protocol Support

The indexer is designed to integrate with IDE plugins via Language Server Protocol (LSP):

#### Capabilities

1. **Function Discovery**: Real-time discovery of doeff functions
2. **Type Hints**: Provide type information for function parameters  
3. **Documentation**: Show function purpose and categorization
4. **Navigation**: Jump to function definitions
5. **Completion**: Auto-complete for doeff function names

#### LSP Requests

**Index Request:**
```json
{
  "method": "doeff/index",
  "params": {
    "rootUri": "file:///project/path"
  }
}
```

**Find Request:**  
```json
{
  "method": "doeff/find",
  "params": {
    "type": "kleisli",
    "typeArg": "str",
    "rootUri": "file:///project/path"
  }
}
```

**Response Format:**
```json
{
  "result": {
    "entries": [
      {
        "name": "process_string",
        "moduleUri": "file:///project/mymod.py",
        "line": 15,
        "character": 4,
        "categories": ["KleisliProgram", "DoFunction"],
        "signature": "process_string(value: str) -> int"
      }
    ]
  }
}
```

### IDE Plugin Features

#### PyCharm Plugin
- **Gutter Icons**: Visual indicators for doeff functions
- **Quick Actions**: Convert between function types
- **Inspection**: Validate doeff patterns
- **Navigation**: "Go to" commands for related functions

#### VS Code Extension  
- **Tree View**: Sidebar showing categorized functions
- **Hover Information**: Type details on hover
- **Command Palette**: Quick access to find commands
- **Syntax Highlighting**: Special highlighting for `@do` and markers

### Configuration

IDE plugins can configure indexer behavior:

```json
{
  "doeff.indexer.includePaths": ["src/", "lib/"],
  "doeff.indexer.excludePaths": ["tests/", "build/"],
  "doeff.indexer.enableRealTime": true,
  "doeff.indexer.showSignaturePreview": true
}
```

## Examples and Edge Cases

### Basic Examples

#### Interpreter Example
```python
def run_program(program: Program[int]) -> int:  # doeff: interpreter
    """✅ Correctly marked interpreter"""
    return program.run()

# Categories: [ProgramInterpreter, AcceptsProgramParam, HasMarker]
# Found by: find-interpreters
```

#### Transform Example  
```python
def map_program(program: Program[int]) -> Program[str]:  # doeff: transform
    """✅ Correctly marked transform"""
    return program.map(str)

# Categories: [ProgramTransformer, AcceptsProgramParam, ReturnsProgram, HasMarker]  
# Found by: find-transforms
```

#### Kleisli Example
```python
@do
def fetch_user(user_id: str) -> User:
    """✅ @do function creating KleisliProgram[str, User]"""
    yield Log(f"Fetching {user_id}")
    return User(user_id)

# Categories: [KleisliProgram, DoFunction]
# Found by: find-kleisli, find-kleisli --type-arg str
```

#### Interceptor Example
```python
def log_interceptor(effect: LogEffect) -> LogEffect:  # doeff: interceptor
    """✅ Correctly marked interceptor"""
    return LogEffect(f"[LOGGED] {effect.message}")

# Categories: [Interceptor, AcceptsEffectParam, HasMarker]
# Found by: find-interceptors
```

### Edge Cases

#### Multiple Markers
```python
def hybrid(program: Program[Any]) -> Program[Any]:  # doeff: transform, interpreter
    """Function with multiple markers - appears in both find-transforms and find-interpreters"""
    return program

# Categories: [ProgramTransformer, AcceptsProgramParam, ReturnsProgram, HasMarker]
# Found by: find-transforms, find-interpreters  
```

#### Incorrect Markers
```python
def wrong_interpreter(program: Program[int]) -> Program[int]:  # doeff: interpreter
    """❌ Marked as interpreter but returns Program (should be transform)"""
    return program

# Categories: [ProgramTransformer, AcceptsProgramParam, ReturnsProgram, HasMarker]
# Found by: find-interpreters (marker takes precedence)
# Warning: Signature suggests transform but marked as interpreter
```

#### @do with Program Parameter
```python
@do
def do_transform(program: Program[int]) -> str:  # doeff: transform
    """@do with Program param -> automatically categorized as Transform"""
    result = yield program
    return str(result)

# Categories: [ProgramTransformer, DoFunction, AcceptsProgramParam]
# Found by: find-kleisli (due to @do), find-transforms (due to marker)
```

#### Unmarked Functions
```python
def unmarked_interpreter(program: Program[str]) -> str:
    """❌ Valid interpreter signature but no marker - not found by find-*"""
    return program.run()

# Categories: [ProgramInterpreter, AcceptsProgramParam]  
# Found by: (none - no markers)
```

#### Class Methods
```python
class Executor:
    def run(self, program: Program[int]) -> int:  # doeff: interpreter
        """✅ Class method interpreter"""
        return program.run()
    
    @do
    def fetch(self, key: str) -> Data:
        """✅ Class method Kleisli"""
        yield Log(f"Fetching {key}")
        return Data(key)

# Both functions are properly indexed and categorized
```

#### Type Filtering Edge Cases
```python
@do
def optional_param(value: Optional[str]) -> int:
    """Matches --type-arg str due to Optional[str] containing str"""
    return len(value or "")

@do  
def union_param(value: Union[str, int]) -> str:
    """Matches --type-arg str due to Union containing str"""
    return str(value)

@do
def any_param(value: Any) -> Result:
    """Matches ALL --type-arg filters due to Any"""
    return Result(value)

# All found by: find-kleisli --type-arg str
```

#### Complex Generics
```python
@do
def complex_generic(data: Dict[str, List[User]]) -> Summary:
    """Matches --type-arg User due to nested User type"""
    return analyze_users(data)

# Found by: find-kleisli --type-arg User
```

#### Async Functions
```python
async def async_interpreter(program: Program[str]) -> str:  # doeff: interpreter
    """✅ Async functions are supported"""
    return await program.async_run()

# Categories: [ProgramInterpreter, AcceptsProgramParam, HasMarker]
# Found by: find-interpreters
```

#### Property Methods
```python
class Manager:
    @property
    def interpreter(self) -> Callable:  # doeff: interpreter
        """✅ Property methods are supported"""
        return lambda p: p.run()

# Categories: [HasMarker] (limited categorization due to lambda return)
# Found by: find-interpreters
```

### Error Cases

#### Missing Markers
```python
def valid_but_unmarked(program: Program[int]) -> int:
    """Valid interpreter but missing marker - won't be found"""
    return program.run()

# Result: Categorized but not discoverable via find-interpreters
```

#### Invalid Syntax
```python
def broken_syntax(program: Program[int] -> int:  # Syntax error
    return program.run()

# Result: Skipped during parsing with warning logged
```

#### Missing Type Annotations
```python
def no_annotations(program):  # doeff: interpreter
    """Missing type annotations - limited categorization"""
    return program.run()

# Categories: [HasMarker]
# Found by: find-interpreters (marker-based)
```

## Implementation Notes

### Performance Considerations

1. **Caching**: Index results should be cached per project
2. **Incremental Updates**: Support incremental re-indexing for modified files
3. **Parallel Processing**: Parse multiple files concurrently
4. **Memory Usage**: Stream large projects instead of loading all in memory

### Error Handling

1. **Syntax Errors**: Skip malformed files with warnings
2. **Import Errors**: Continue indexing despite missing dependencies  
3. **Type Resolution**: Handle unresolved type annotations gracefully
4. **File Access**: Handle permission errors and missing files

### Extensibility

1. **Custom Markers**: Support project-specific marker prefixes
2. **Plugin System**: Allow custom categorization rules
3. **Export Formats**: Support multiple output formats (JSON, XML, etc.)
4. **IDE Integration**: Pluggable IDE adapters

This specification serves as the authoritative reference for implementing and maintaining the doeff-indexer tool.
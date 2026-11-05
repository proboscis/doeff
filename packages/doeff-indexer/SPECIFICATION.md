# doeff-indexer Specification

## Table of Contents

1. [Purpose and Overview](#purpose-and-overview)
2. [Type Definitions](#type-definitions)
3. [Detection Logic](#detection-logic)
4. [CLI Commands](#cli-commands)
   - [Hy Index (`hy-index`)](#hy-index-hy-index)
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

Returns functions marked with `# doeff: kleisli`.

```bash
find-kleisli /project/path
```

**Filtering Logic:**
- Has `kleisli` in markers array

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
- Rely exclusively on marker-based filtering
- Return JSON arrays of matching entries
- Exit with status 0 on success, non-zero on error

## Hy Index (`hy-index`)

Emits the definitions, imports, references, and calls of Hy files (`*.hy` / `*.hyk` / `*.hyp`) as JSON. It is for editor go-to-definition, reference search, outlines, and navigating between effects, handlers, and defk. It is independent of the Python index (the commands above) and does not build a Python index. The implementation is in `src/hy_index/`.

### Usage

```
doeff-indexer hy-index --root <dir>                        # all Hy files under dir
doeff-indexer hy-index --root <dir> --file <path>…         # only the specified files (root is the basis for module names)
doeff-indexer hy-index --root <dir> --stdin --path <path>  # read unsaved content from stdin and emit it as one file at path
```

- Discovery does not descend into the directories `.venv`, `node_modules`, `target`, `.git`, `__pycache__`.
- Relative paths of `--file` and `--path` are resolved against `--root`. `--output` and `--pretty` are the same as the other commands.
- It does not stop on unreadable files or unclosed brackets. It emits what could be read and records the reason in that file's `errors` (`line:column: …`, 1-based). The exit code is 0. Only argument errors (`--stdin` without `--path`, `--root` not being a directory, etc.) give 2.

### Output shape (contract version 6)

The raw side-effect evidence added in version 3 (`raw`, `raw_via`, `raw_catalog_problems`) and the definition tags (`tags`) are in `experiments/hy-highlighter/hy-index-contract-v3.md`. The fully qualified names added in version 4 (`qualified_name`, `target`), the declared effects, types, and contracts added in version 5 (`effects`, `param_types`, `answer_type`, `contracts`, `handles`), and the definition decorators added in version 6 (`decorators`) have this section as their source of truth.

```jsonc
{
  "version": 6,
  "root": "/abs/root",
  "files": [{
    "path": "/abs/root/pkg/mod.hy",
    "module": "pkg.mod",            // path relative to root. __init__.hy is the parent package name
    "definitions": [{
      "name": "classifier-peers",   // spelling as written
      "mangled": "classifier_peers",// - becomes _ (a leading - is kept)
      "qualified_name": "pkg.mod.classifier_peers", // fully qualified name (version 4) = module + container (if any) + name. Every segment is the mangled spelling
      "kind": "defn",
      "range": {…},                 // position of the name
      "full_range": {…},            // the whole form
      "container": null,            // name of the containing definition (method, field, enum-member, effect-clause, nested law, etc.)
      "docstring": "…",             // null if none
      "params": ["jev-script", "store"],
      "bases": []                   // base symbols of defclass / defrecord (as written; dotted is one). Always [] for other kinds
      // "checks": ["(>= start 0)"] // expressions of :check in the head dict of defrecord (as written, in written order). Only a defrecord with a head dict has it; others omit the field (addition to version 3)
      "effects": [{"name": "ReadInput", "target": "pkg.intent.ReadInput"}],  // declared effects (version 5). null if there is no :effects
      "param_types": [{"name": "request", "type": {"text": "InputRequest", "names": [{"name": "InputRequest", "target": "pkg.types.InputRequest"}]}}],
      "answer_type": {"text": "(| Judgment None)", "names": [{"name": "Judgment", "target": "pkg.types.Judgment"}, {"name": "None", "target": null}]},
      "contracts": [{"side": "pre", "text": "(> budget 0)"}],          // non-type contract predicates (version 5)
      "handles": null,                                                   // effect clause only: the effect it resolves {"name", "target"} (version 5)
      "decorators": ["dataclass :frozen True"]                           // spellings of the definition's decorators (version 6). [] if none
    }],
    "imports": [{"module": "doeff_records._memory", "name": "MemoryStore", "alias": null, "range": {…}, "is_require": false}],
    "references": [{"name": "c", "mangled": "c", "qualifier": "a.b", "range": {…}}],
    "calls": [{"callee": "PutRow", "mangled": "PutRow", "qualifier": null, "range": {…}, "caller": 12, "performed": true,
               "target": "pkg.effects.PutRow"}],  // target (version 4) = fully qualified name of the callee. null if it cannot be resolved
    "errors": []
  }]
}
```

- Positions are 0-based lines and columns in UTF-16 code units (the same as VS Code's Position. A Japanese character is 1, an emoji is 2).
- kind: `defn` `defn/a` `defmacro` `defk` `deff` `defp` `defpp` `fnk-binding` (not emitted for now) `defclass` `defrecord` `defenum` `enum-member` `field` `method` `defhandler` `effect-clause` `deftest` `defadr` `defsemgrep` `law` `defpipeline` `defworkflow` `defphase` `defmcp-tool` `deftype` `defmain` `variable`.
- `definitions` are only the top-level definitions of the file and the nested ones directly under them. The inside of `(do …)` and `(eval-and-compile …)` is treated as top level. Local bindings inside a function are not included.
  - Body of defclass: `(defn …)` is `method`; `#^ T x`, `(#^ T x default)`, and `(setv x …)` are `field`.
  - Fields of defrecord, members of defenum (both `A` and `(A "value")`).
  - defhandler: `(Effect [fields] …)` is `effect-clause` (params = fields); `(session val|var x …)` and the old `(lazy-val …)` / `(lazy-var …)` are `variable`. Both have container = the handler name.
  - `law`, `defsemgrep`, and `deftest` inside defadr, and `defphase` inside defworkflow, have container = the enclosing name.
  - `variable` = top-level `setv` / `setx` (including `[a b]` destructuring), `val` / `var`, `(lazy val …)`, `(session val|var …)`, `lazy-val` / `lazy-var`.
- docstring: for function forms, the string after the arguments (it may also come after the `{:pre … :post …}` of defk / deff), only when another form follows. For class, record, and enum, the string at the head of the body. For defhandler, after the name or after the arguments. For `law`, `:statement`; for `defadr`, `:title`; for `defmcp-tool`, the description string. Indentation is aligned the same way as Python's `inspect.cleandoc`.
- `imports`: `(import m)`, `(import m :as a)`, `(import m [x y :as z])`, `(import m *)`, `(require m [names])`, `(require m :macros [..] :readers [..])`. The range points at whichever exists in the order alias -> name -> module. Imports inside functions are also included.
- `references`: every occurrence of a symbol is included one by one, split at `.` (`a.b.c` gives a, b, c; the qualifier of c is `"a.b"`). Not included: reserved words placed at the head (Hy special forms and doeff-hy macros; macros with ordinary words such as `val` are reserved words only when the file requires them), operators, constants (`True` / `False` / `None`, etc.), `_`, keywords (`:key`), the content of strings, comments, forms discarded with `#_`, and the inside of quote (`~x` inside quasiquote is included). Symbols inside `{…}` of an f-string are included.
- `bases` (version 2): only the symbols inside the `[…]` of defclass, as written (keywords such as `:metaclass M` with their values, and expressions such as `(get Generic T)`, are not included). defrecord is a form where the current macro does not write bases, so it is usually `[]`.
- `checks` (addition to version 3): each expression of `:check` in the head dict of `(defrecord Name "doc"? {:tags {…} :check […]} fields …)`, as spelled and in written order. A defrecord with a head dict gives `[]` if there is no `:check`; a defrecord without a head dict and other kinds omit the field. The `:tags` of the head dict go into `tags` the same as defk / defeffect. The head dict is not a field (fields are read after the dict).
- `calls` (version 2): each symbol right after `(` that is not a reserved word, operator, or constant is included one by one (function calls, class construction, effect construction). Nested calls inside arguments are also included.
  - `callee` is the last segment of the head symbol, `qualifier` is the segments before it (`"mod.sub"` for `mod.sub.fn`), and `range` is the position of the last segment.
  - `caller` is the index (into the same file's `definitions`) of the narrowest definition whose `full_range` contains the position of the call. If no definition contains it (a top-level expression), `null`. Inside the body of an effect clause it is that `effect-clause`; inside a local function within `(fn …)` or `let` it is the enclosing definition. The `f` of `(setv x (f))` has the `variable` of `x` as its caller.
  - `performed` is true for X in `(<- (X …))`, `(<- name (X …))`, `(<- name T (X …))`, and for the call directly under `yield` / `yield-from` / `!` (doeff-hy's effect bind in argument position, `(! (X …))`). A symbol-only form such as `(! x)` is not a call. Nested calls in its arguments are false.
  - `target` (version 4): the fully qualified name of the callee. It is the result of name resolution decided only from the file's own contents (module, definitions, imports), and does not look at other files. So it is the same value for any of the `--root`, `--file`, and `--stdin` runs. Resolution order:
    1. Definitions in the same file -- an unqualified name is a top-level definition (`container` is null); `q.name` is a definition inside container q (`Store.put` -> `pkg.mod.Store.put`).
    2. Imports of the file (excluding `require` -- macros are not made callee definitions). The first binding import is used, and relative imports are resolved against the package of the file where they are written. `(y)` in `(import m [x :as y])` is `m.x`; `(m.sub.f)` in `(import m)` is `m.sub.f`; `(sub.f)` in `(import pkg [sub])` and `(C.f)` in `(import m [C])` have the shapes `pkg.sub.f` / `m.C.f`.
    3. Anything else (builtins, local bindings, arguments, forms that call the module itself) is null.
  - `target` is not necessarily a Hy definition (a Python name such as `json.dumps` also becomes its fully qualified name). If it matches a `qualified_name` of the index, it is a Hy definition. `qualified_name` can overlap because of redefinitions of the same name in the same file, so a match is treated as multiple.
  - The index does not hold a reverse lookup of callers (definitions that call a given definition, lists of deftest). The reading side reverses the match between `target` and `qualified_name` (so that the calls of a file replaced in a single-file `--stdin` run also correctly affect the reverse lookups of other files).
  - A call is assigned only to the `caller` definition (the innermost). The calls of outer definitions (defclass, defhandler, defadr, etc.) are added by the reading side for the nested definitions contained in their `full_range`.
  - The via evidence of raw side effects (`raw.via`) also looks up the call destination by this match of `target` and `qualified_name` (name resolution lives in one place, `src/hy_index/qualify.rs`).
  - The fields of version 5 (declared effects, types, contracts) hold only the written facts and name resolution. How to read the meaning of types (Union, Maybe, Raise, and effect `:answer` / `:absent` / `:failure`) and the **inferred effects** have `signatures` of doeff-linter's editor-json as their source of truth (inference is a fixed point over the whole repo and cannot be produced by a single-file run; DOEFF127 checks that the `:effects` declaration matches the inference).
    - `effects`: the names in `:effects [E …]` of definitions that have a contract dict (defk, deff, defp, defpp, defhandler), in written order. null if there is no `:effects`; `[]` is a declaration of "raises nothing". null for other kinds.
    - `param_types`: `(: arg type)` in the contract dict's `:pre` (only those that correspond to an argument name, in argument order, the first one for the same argument), `(: field type)` in defeffect's `:fields`, and `#^ type` on fields of defrecord / defwire and defclass (from version 6) (all read the fields.rs way -- parenthesized, bare, `(setv #^ T x v)`). Arguments and fields with no written type are not included (the field-definition kind `field` is as before).
    - `answer_type`: `(: % type)` in `:post`, otherwise the `#^ type` on the name (defn / defk / deff). For defeffect, `:answer`. null if none.
    - `contracts`: those predicates of `:pre` / `:post` that did not become the type annotations above (`(> budget 0)`, `(: self.x T)` on a name that is not an argument, a second annotation for the same argument), as written. `side` is `pre` / `post`.
    - A type annotation is `{"text": spelling as written, "names": [...]}`. `names` are the symbols inside the annotation, once each in written order (syntax `|`, `of`, `get`, operators, keywords, strings, and numbers are excluded).
    - `handles`: only definitions whose kind is `effect-clause` have `{"name": spelling of the clause head, "target": fully qualified name of the effect}`; others are null.
  - `decorators` (version 6): each element of the `[…]` of `(defclass [d …] Name …)` and `(defn [d …] name …)` (also defn/a, defmacro, defk, deff, and methods inside a class), in written order. The spelling is as written; call forms drop the outer parentheses (`[(dataclass :frozen True)]` -> `"dataclass :frozen True"`, `[dataclass]` -> `"dataclass"`), and runs of whitespace outside strings (including newlines) are collapsed into one (inside strings is left as is). Elements discarded with `#_` are not included. A definition with no decorator, and kinds that cannot have decorators, give `[]`.
    - The `target` of a name uses the same name resolution as a call's `target` (definition in the same file -> import other than `require` -> null). Python effects (`doeff_core_effects.…`) also become fully qualified names.
    - The reverse lookups (the handler that resolves that effect, definitions that accept / return that type, definitions that use that effect) are not held in the index; the reading side reverses the match between `target` and `qualified_name` (same reason as U7's callers -- so that the part of a file replaced in a single-file run correctly affects the reverse lookups of other files). Resolving handler = the definition in the `container` of the effect clause whose `handles.target` matches.
  - Not included: reserved words (including macros of ordinary words that were required), heads of doeff-hy binding syntax (`val` / `var` / `lazy` / `session`; they are syntax even without a require because macros such as defk read them), `(.method obj)`, the method in `(. obj (method …))`, the head of an effect clause of defhandler / `handle` (the `PutRow` of `(PutRow [table key] …)`), inside type annotations (`#^ (of list int) x`), inside match patterns (`(Point :x px)`), and inside quote. These symbols are still included in `references` as before.

### Known limitations

- mangle is only the simple form of the contract (`-` -> `_`) and does not convert Hy symbols such as `?` and `!` (`hyx_…`).
- Definitions inside conditions such as `(when …)`, and definition forms not in the list above such as `defdomain`, are not included in `definitions` (they are included in references). The `caller` of calls inside them is the enclosing definition or `null`.
- `(f a)` inside `->` / `->>` is included as a call with the head of the written form (the argument order after expansion is not looked at).
- The format of `{x:>10}` in an f-string treats everything before `:` as the name. Hy's style of separating with a space, such as `{x !r}`, is read correctly.

## Type Filtering Rules

### Type Annotation Matching

The indexer supports sophisticated type matching for parameter filtering:

#### Exact Type Matching
```python
# find-kleisli --type-arg str matches:
@do
def process_string(value: str) -> int:  # doeff: kleisli  # ✅ Exact match
    return len(value)
```

#### Generic Type Matching  
```python
# find-kleisli --type-arg str matches:
@do  
def process_optional(value: Optional[str]) -> int:  # doeff: kleisli  # ✅ Contains str
    return len(value or "")

@do
def process_list(items: List[str]) -> int:  # doeff: kleisli  # ✅ Contains str  
    return len(items)
```

#### Any Type Special Handling
```python
@do
def process_any(value: Any) -> str:  # doeff: kleisli  # ✅ Matches ALL type filters
    return str(value)
```

**Any Matching Logic:**
- Functions with `Any` type parameter match all `--type-arg` filters
- This allows generic functions to appear in all type-specific searches

#### Union Type Handling
```python
@do
def process_union(value: Union[str, int]) -> str:  # doeff: kleisli  # ✅ Matches --type-arg str
    return str(value)
```

#### Complex Generics
```python  
@do  
def process_complex(data: Dict[str, List[User]]) -> Summary:  # doeff: kleisli  # ✅ Matches --type-arg User
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

### Kleisli Type-Arg Constraints

When clients call `find-kleisli --type-arg Program[T]`, the indexer extracts the inner `T` and
returns only entries that model `Kleisli[T, _]`:

- Only functions marked with `# doeff: kleisli` are eligible for CLI results.
- Type filtering additionally requires the function to use `@do`.
- Exactly one positional parameter may lack a default value; it must be annotated as `T` or `Any`.
- Additional parameters are allowed only when they provide defaults, because the CLI cannot infer
  values for extra required arguments.
- Functions with multiple required parameters are excluded from Kleisli results.
- Parameters typed as `Any` continue to match every `--type-arg` filter after the `Program[T]`
  unwrapping step.

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

### Marker Requirements for CLI Commands

Specific CLI discovery commands (`find-interpreters`, `find-transforms`, `find-kleisli`,
`find-interceptors`) only return entries that carry an explicit marker comment. Categorization
still honours signature analysis and decorators, but discoverability via these commands requires a
matching marker:

- `find-interpreters` → `# doeff: interpreter`
- `find-transforms` → `# doeff: transform`
- `find-kleisli` → `# doeff: kleisli`
- `find-interceptors` → `# doeff: interceptor`

`@do` functions continue to be categorized automatically (Kleisli/Transform/Interceptor), yet they
must also be marked to appear in CLI results. This guarantees that IDE integrations and automation
only surface functions that were intentionally exposed by maintainers.

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
